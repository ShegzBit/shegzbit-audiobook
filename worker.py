"""
worker.py — Background job queue using ThreadPoolExecutor + SQLite.
No Redis required; jobs are persisted in the DB so status survives queries.

Uses the new modular scraping pipeline with configurable retries.
"""

import logging
import os
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from typing import Optional

from curl_cffi.requests.exceptions import HTTPError, RequestException

from browser import manager as browser_manager
from database import SessionLocal
from errors import CaptchaError, EmptyExtractionError, HttpError, NetworkError, ScrapingError
from extractor import extract_chapter, fetch_html
from models import Chapter, Episode, Job, Novel
from retry import retry
from synthesizer import synthesize

logger = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=2)

# Per-domain rate limiting: ensure min 2s between requests to same domain
_domain_last_request: dict[str, float] = defaultdict(float)
_domain_lock = Lock()
DOMAIN_MIN_DELAY = 2.0


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc


def _rate_limit_domain(url: str):
    domain = _domain_of(url)
    with _domain_lock:
        last = _domain_last_request[domain]
        wait = DOMAIN_MIN_DELAY - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _domain_last_request[domain] = time.time()


def _set_status(job_id: str, status: str, **kwargs):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = status
            job.updated_at = datetime.utcnow()
            for k, v in kwargs.items():
                setattr(job, k, v)
            db.commit()
    except Exception:
        logger.exception(f"Failed to set status {status} for job {job_id}")
    finally:
        db.close()


def _find_or_create_novel(db, novel_title: Optional[str], novel_index_url: Optional[str]) -> Optional[Novel]:
    if not novel_title:
        return None
    novel = db.query(Novel).filter(Novel.title == novel_title).first()
    if not novel:
        novel = Novel(title=novel_title, source_index_url=novel_index_url)
        db.add(novel)
        db.flush()
    elif novel_index_url and not novel.source_index_url:
        novel.source_index_url = novel_index_url
    return novel


# ── Retry-wrapped fetch + extract ───────────────────────────────────────────

@retry(
    max_attempts=3,
    backoff_base=2.0,
    retryable_errors=(NetworkError,),
)
def _fetch_with_retry(url: str) -> str:
    """Fetch HTML with automatic retries on transient network errors."""
    return fetch_html(url)


@retry(
    max_attempts=2,
    backoff_base=3.0,
    retryable_errors=(NetworkError,),
)
def _extract_with_retry(url: str, html: str):
    """Extract chapter with retry on transient errors."""
    return extract_chapter(url, html)


# ── Job processing ──────────────────────────────────────────────────────────

def _save_chapter_record(job_id: str, chapter, db=None):
    """Save the completed chapter and episode records to the DB."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        novel = _find_or_create_novel(db, chapter.novel_title, chapter.novel_index_url)

        ch = Chapter(
            url_hash=str(uuid.uuid4()),
            url=job.url,
            voice=job.voice,
            rate=job.rate,
            title=chapter.title,
            word_count=chapter.word_count,
            audio_path=chapter.audio_path if hasattr(chapter, "audio_path") else "",
            next_chapter_url=chapter.next_chapter_url,
            novel_id=novel.id if novel else None,
            source_name=chapter.source_name,
        )
        db.add(ch)
        db.flush()

        if novel:
            ep = db.query(Episode).filter(
                Episode.novel_id == novel.id,
                Episode.chapter_url == job.url,
            ).first()
            if not ep:
                ep = Episode(
                    novel_id=novel.id,
                    chapter_id=ch.id,
                    chapter_url=job.url,
                )
                db.add(ep)
            elif not ep.chapter_id:
                ep.chapter_id = ch.id

        job.status = "done"
        job.chapter_id = ch.id
        job.updated_at = datetime.utcnow()
        db.commit()
    finally:
        if close_db:
            db.close()


def process_job(job_id: str):
    """Process a single scrape + synthesize job."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        # Fetch
        _set_status(job_id, "fetching")
        _rate_limit_domain(job.url)

        try:
            html = _fetch_with_retry(job.url)
        except CaptchaError as e:
            _set_status(job_id, "captcha_blocked", error_msg=str(e))
            return
        except HttpError as e:
            _set_status(job_id, "error",
                        error_msg=f"HTTP error {e.status_code}: {e}")
            return
        except NetworkError as e:
            _set_status(job_id, "error",
                        error_msg=f"Network error after retries: {e}")
            return

        # Extract
        _set_status(job_id, "extracting")
        try:
            chapter = _extract_with_retry(job.url, html)
        except CaptchaError as e:
            _set_status(job_id, "captcha_blocked", error_msg=str(e))
            return
        except EmptyExtractionError as e:
            _set_status(job_id, "error", error_msg=str(e))
            return
        except ScrapingError as e:
            _set_status(job_id, "error", error_msg=str(e))
            return

        # Synthesize
        _set_status(job_id, "synthesizing")

        def _on_synthesis_progress(current: int, total: int):
            pct = int(current / total * 100)
            _set_status(job_id, "synthesizing",
                        progress_pct=pct,
                        progress_msg=f"Chunk {current} of {total}")

        audio_path = synthesize(chapter.text, job.voice, job.rate, chapter.title,
                                progress_callback=_on_synthesis_progress)

        # Save
        db2 = SessionLocal()
        try:
            job2 = db2.query(Job).filter(Job.id == job_id).first()
            novel = _find_or_create_novel(db2, chapter.novel_title, chapter.novel_index_url)

            ch = Chapter(
                url_hash=str(uuid.uuid4()),
                url=job2.url,
                voice=job2.voice,
                rate=job2.rate,
                title=chapter.title,
                word_count=chapter.word_count,
                audio_path=str(audio_path),
                next_chapter_url=chapter.next_chapter_url,
                novel_id=novel.id if novel else None,
                source_name=chapter.source_name,
            )
            db2.add(ch)
            db2.flush()

            if novel:
                ep = db2.query(Episode).filter(
                    Episode.novel_id == novel.id,
                    Episode.chapter_url == job2.url,
                ).first()
                if not ep:
                    ep = Episode(
                        novel_id=novel.id,
                        chapter_id=ch.id,
                        chapter_url=job2.url,
                    )
                    db2.add(ep)
                elif not ep.chapter_id:
                    ep.chapter_id = ch.id

            job2.status = "done"
            job2.chapter_id = ch.id
            job2.updated_at = datetime.utcnow()
            db2.commit()
        finally:
            db2.close()

    except Exception as e:
        logger.exception(f"Unhandled error in job {job_id}: {e}")
        _set_status(job_id, "error", error_msg=f"Unexpected error: {e}")
    finally:
        db.close()


def process_job_from_html(job_id: str, html: str):
    """Process a job with pre-fetched HTML (manual paste flow)."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        _set_status(job_id, "extracting")
        try:
            chapter = extract_chapter(job.url, html)
        except CaptchaError as e:
            _set_status(job_id, "captcha_blocked", error_msg=str(e))
            return
        except EmptyExtractionError as e:
            _set_status(job_id, "error", error_msg=str(e))
            return
        except ScrapingError as e:
            _set_status(job_id, "error", error_msg=str(e))
            return

        _set_status(job_id, "synthesizing")

        def _on_synthesis_progress(current: int, total: int):
            pct = int(current / total * 100)
            _set_status(job_id, "synthesizing",
                        progress_pct=pct,
                        progress_msg=f"Chunk {current} of {total}")

        audio_path = synthesize(chapter.text, job.voice, job.rate, chapter.title,
                                progress_callback=_on_synthesis_progress)

        db2 = SessionLocal()
        try:
            job2 = db2.query(Job).filter(Job.id == job_id).first()
            novel = _find_or_create_novel(db2, chapter.novel_title, chapter.novel_index_url)

            ch = Chapter(
                url_hash=str(uuid.uuid4()),
                url=job2.url,
                voice=job2.voice,
                rate=job2.rate,
                title=chapter.title,
                word_count=chapter.word_count,
                audio_path=str(audio_path),
                next_chapter_url=chapter.next_chapter_url,
                novel_id=novel.id if novel else None,
                source_name=chapter.source_name,
            )
            db2.add(ch)
            db2.flush()

            if novel:
                ep = db2.query(Episode).filter(
                    Episode.novel_id == novel.id,
                    Episode.chapter_url == job2.url,
                ).first()
                if not ep:
                    ep = Episode(
                        novel_id=novel.id,
                        chapter_id=ch.id,
                        chapter_url=job2.url,
                    )
                    db2.add(ep)
                elif not ep.chapter_id:
                    ep.chapter_id = ch.id

            job2.status = "done"
            job2.chapter_id = ch.id
            job2.updated_at = datetime.utcnow()
            db2.commit()
        finally:
            db2.close()

    except Exception as e:
        logger.exception(f"Unhandled error in job {job_id}: {e}")
        _set_status(job_id, "error", error_msg=f"Unexpected error: {e}")
    finally:
        db.close()


def enqueue_job(url: str, voice: str, rate: str) -> str:
    db = SessionLocal()
    try:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, url=url, voice=voice, rate=rate, status="queued")
        db.add(job)
        db.commit()
    finally:
        db.close()

    executor.submit(process_job, job_id)
    return job_id


def enqueue_job_with_html(url: str, html: str, voice: str, rate: str) -> str:
    db = SessionLocal()
    try:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, url=url, voice=voice, rate=rate, status="queued")
        db.add(job)
        db.commit()
    finally:
        db.close()

    executor.submit(process_job_from_html, job_id, html)
    return job_id


def shutdown():
    executor.shutdown(wait=False)
    browser_manager.shutdown()

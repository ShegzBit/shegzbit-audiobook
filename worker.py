"""
worker.py — Background job queue using ThreadPoolExecutor + SQLite.
No Redis required; jobs are persisted in the DB so status survives queries.
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

import requests

from database import SessionLocal
from extractor import CaptchaError, extract_chapter, fetch_html, is_captcha_page
from models import Chapter, Episode, Job, Novel
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


def process_job(job_id: str):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        # Fetch
        _set_status(job_id, "fetching")
        _rate_limit_domain(job.url)

        try:
            html = fetch_html(job.url)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code in (403, 429):
                _set_status(job_id, "captcha_blocked",
                            error_msg=f"HTTP {code} — the site may require captcha verification.")
                return
            _set_status(job_id, "error", error_msg=f"HTTP error {code}: {e}")
            return
        except requests.RequestException as e:
            _set_status(job_id, "error", error_msg=f"Network error: {e}")
            return

        # Extract (captcha detection is deferred inside extract_chapter — we try
        # to pull text first and only raise CaptchaError if extraction fails)
        _set_status(job_id, "extracting")
        try:
            chapter = extract_chapter(job.url, html)
        except CaptchaError as e:
            _set_status(job_id, "captcha_blocked", error_msg=str(e))
            return
        except ValueError as e:
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

        # Save chapter record and update job
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


def shutdown():
    executor.shutdown(wait=False)

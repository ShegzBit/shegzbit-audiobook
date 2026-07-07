"""
main.py — FastAPI application for Web Novel TTS.
Phases 1-5: pipeline, job queue, library, captcha handling, auth + RSS.
"""

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    SHARED_PASSWORD,
    check_password,
    is_authenticated,
    make_auth_cookie,
)
from database import SessionLocal, get_db, init_db
from models import Chapter, Episode, Job, Novel
from worker import enqueue_job, shutdown


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("output", exist_ok=True)
    init_db()
    db = SessionLocal()
    try:
        stuck = db.query(Job).filter(
            Job.status.in_(["queued", "fetching", "extracting", "synthesizing"])
        ).all()
        for j in stuck:
            j.status = "error"
            j.error_msg = "Server restarted while this job was running — please retry."
        db.commit()
    finally:
        db.close()
    yield
    shutdown()


app = FastAPI(title="Web Novel TTS", lifespan=lifespan)
app.mount("/output", StaticFiles(directory="output"), name="output")
app.mount("/static", StaticFiles(directory="static"), name="static")
_jinja_env = Environment(loader=FileSystemLoader("templates"), autoescape=True, cache_size=0)
templates = Jinja2Templates(env=_jinja_env)

VOICES = {
    "en-US-BrianNeural": "Brian — US Male (Warm)",
    "en-US-AndrewNeural": "Andrew — US Male",
    "en-US-EmmaNeural": "Emma — US Female (Warm)",
    "en-US-AvaNeural": "Ava — US Female",
    "en-GB-RyanNeural": "Ryan — UK Male",
    "en-GB-SoniaNeural": "Sonia — UK Female",
    "en-AU-WilliamNeural": "William — AU Male",
    "en-AU-NatashaNeural": "Natasha — AU Female",
}

DEFAULT_VOICE = "en-US-BrianNeural"
DEFAULT_RATE = "+0%"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_redirect(request: Request):
    if not is_authenticated(request):
        return RedirectResponse("/login", status_code=302)
    return None


def _api_auth(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")


def _xml_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _chapter_dict(ch: Chapter) -> dict:
    return {
        "id": ch.id,
        "title": ch.title,
        "word_count": ch.word_count,
        "audio_url": f"/output/{os.path.basename(ch.audio_path)}",
        "next_chapter_url": ch.next_chapter_url,
        "novel_id": ch.novel_id,
    }


def _episode_dict(ep: Episode) -> dict:
    return {
        "id": ep.id,
        "chapter_url": ep.chapter_url,
        "chapter_number": ep.chapter_number,
        "listened_position_seconds": ep.listened_position_seconds,
        "chapter": _chapter_dict(ep.chapter) if ep.chapter else None,
    }


# ---------------------------------------------------------------------------
# HTML Pages
# ---------------------------------------------------------------------------

@app.get("/")
async def index(request: Request):
    redir = _auth_redirect(request)
    if redir:
        return redir
    return templates.TemplateResponse(request, "index.html", {
        "voices": VOICES,
        "default_voice": DEFAULT_VOICE,
        "default_rate": DEFAULT_RATE,
        "auth_enabled": bool(SHARED_PASSWORD),
    })


@app.get("/library")
async def library(request: Request, db: Session = Depends(get_db)):
    redir = _auth_redirect(request)
    if redir:
        return redir
    novels = db.query(Novel).order_by(Novel.created_at.desc()).all()
    novel_data = []
    for n in novels:
        episodes = (
            db.query(Episode)
            .filter(Episode.novel_id == n.id)
            .order_by(Episode.created_at.asc())
            .all()
        )
        latest = next((e for e in reversed(episodes) if e.chapter_id), None)
        novel_data.append({
            "novel": n,
            "episodes": episodes,
            "episode_count": len(episodes),
            "latest": latest,
        })
    return templates.TemplateResponse(request, "library.html", {
        "novel_data": novel_data,
        "auth_enabled": bool(SHARED_PASSWORD),
    })


@app.get("/login")
async def login_page(request: Request):
    if not SHARED_PASSWORD:
        return RedirectResponse("/", status_code=302)
    if is_authenticated(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    password = str(form.get("password", ""))
    if check_password(password):
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            COOKIE_NAME, make_auth_cookie(),
            max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
        )
        return response
    return templates.TemplateResponse(request, "login.html", {"error": "Incorrect password."})


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# API — Jobs
# ---------------------------------------------------------------------------

class SubmitRequest(BaseModel):
    url: str
    voice: str = DEFAULT_VOICE
    rate: str = DEFAULT_RATE


@app.post("/api/chapters")
async def submit_chapter(req: SubmitRequest, request: Request):
    _api_auth(request)
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")
    if req.voice not in VOICES:
        raise HTTPException(status_code=400, detail="Invalid voice.")
    job_id = enqueue_job(url, req.voice, req.rate)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    _api_auth(request)
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    result: dict = {
        "job_id": job.id,
        "status": job.status,
        "url": job.url,
        "error": job.error_msg,
        "progress_pct": job.progress_pct,
        "progress_msg": job.progress_msg,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }

    if job.status == "done" and job.chapter:
        ch = job.chapter
        result["chapter"] = _chapter_dict(ch)
        if ch.novel_id:
            ep = (
                db.query(Episode)
                .filter(Episode.novel_id == ch.novel_id, Episode.chapter_url == ch.url)
                .first()
            )
            if ep:
                result["episode"] = {
                    "id": ep.id,
                    "listened_position_seconds": ep.listened_position_seconds,
                }

    return result


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    _api_auth(request)
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in ("captcha_blocked", "error"):
        raise HTTPException(status_code=400, detail="Job is not in a retryable state.")
    new_job_id = enqueue_job(job.url, job.voice, job.rate)
    return {"job_id": new_job_id}


# ---------------------------------------------------------------------------
# API — Novels + Episodes
# ---------------------------------------------------------------------------

@app.get("/api/novels")
async def list_novels(request: Request, db: Session = Depends(get_db)):
    _api_auth(request)
    novels = db.query(Novel).order_by(Novel.created_at.desc()).all()
    result = []
    for n in novels:
        ep_count = db.query(Episode).filter(Episode.novel_id == n.id).count()
        latest = (
            db.query(Episode)
            .filter(Episode.novel_id == n.id, Episode.chapter_id.isnot(None))
            .order_by(Episode.created_at.desc())
            .first()
        )
        result.append({
            "id": n.id,
            "title": n.title,
            "source_index_url": n.source_index_url,
            "episode_count": ep_count,
            "latest_episode": _episode_dict(latest) if latest else None,
            "created_at": n.created_at.isoformat(),
        })
    return result


@app.get("/api/novels/{novel_id}")
async def get_novel(novel_id: int, request: Request, db: Session = Depends(get_db)):
    _api_auth(request)
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found.")
    episodes = (
        db.query(Episode)
        .filter(Episode.novel_id == novel_id)
        .order_by(Episode.created_at.asc())
        .all()
    )
    return {
        "id": novel.id,
        "title": novel.title,
        "source_index_url": novel.source_index_url,
        "episodes": [_episode_dict(ep) for ep in episodes],
        "created_at": novel.created_at.isoformat(),
    }


class QueueNextRequest(BaseModel):
    current_episode_id: int


@app.post("/api/novels/{novel_id}/queue-next")
async def queue_next(
    novel_id: int,
    req: QueueNextRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    _api_auth(request)
    ep = db.query(Episode).filter(Episode.id == req.current_episode_id).first()
    if not ep or not ep.chapter:
        raise HTTPException(status_code=404, detail="Episode or chapter not found.")
    next_url = ep.chapter.next_chapter_url
    if not next_url:
        return {"message": "No next chapter URL detected.", "job_id": None}
    job_id = enqueue_job(next_url, ep.chapter.voice, ep.chapter.rate)
    return {"job_id": job_id, "next_url": next_url}


class PositionUpdate(BaseModel):
    position_seconds: float


@app.patch("/api/episodes/{episode_id}/position")
async def update_position(
    episode_id: int,
    req: PositionUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    _api_auth(request)
    ep = db.query(Episode).filter(Episode.id == episode_id).first()
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found.")
    ep.listened_position_seconds = req.position_seconds
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# RSS Feed (Phase 5)
# ---------------------------------------------------------------------------

@app.get("/rss/{novel_id}.xml")
async def rss_feed(novel_id: int, request: Request, db: Session = Depends(get_db)):
    _api_auth(request)
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found.")

    base_url = str(request.base_url).rstrip("/")
    episodes = (
        db.query(Episode)
        .filter(Episode.novel_id == novel_id, Episode.chapter_id.isnot(None))
        .order_by(Episode.created_at.asc())
        .all()
    )

    items_xml = ""
    for ep in episodes:
        ch = ep.chapter
        if not ch:
            continue
        audio_url = f"{base_url}/output/{os.path.basename(ch.audio_path)}"
        pub_date = ep.created_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
        items_xml += f"""
    <item>
      <title>{_xml_escape(ch.title)}</title>
      <enclosure url="{_xml_escape(audio_url)}" type="audio/mpeg" length="0"/>
      <pubDate>{pub_date}</pubDate>
      <guid>{_xml_escape(ep.chapter_url)}</guid>
    </item>"""

    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{_xml_escape(novel.title)}</title>
    <description>Generated by Web Novel TTS</description>
    <link>{_xml_escape(novel.source_index_url or base_url)}</link>
    <itunes:author>Web Novel TTS</itunes:author>{items_xml}
  </channel>
</rss>"""

    return Response(content=rss_xml, media_type="application/rss+xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)

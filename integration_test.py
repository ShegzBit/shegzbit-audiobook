"""One-shot integration checks for the optimized pipeline.

Creates its own sqlite db + output dir under /tmp/opencode/e2e.
Run: .venv/bin/python integration_test.py
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJ = os.path.dirname(os.path.abspath(__file__))
E2E = tempfile.mkdtemp(prefix="synth_e2e_")
os.chdir(E2E)
sys.path.insert(0, PROJ)

import database
database.init_db()

from models import Job, Chapter
from sources.base import ChapterText
from bench_synthesis import build_sample

import worker
import synthesizer

failures = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}: {name} {detail}")
    if not cond:
        failures.append(name)


# ── Test 1: ffmpeg concat failure falls back to pydub export ───────────────
orig_stitch = synthesizer._stitch_ffmpeg
synthesizer._stitch_ffmpeg = lambda *a, **k: False
try:
    text_fb = ("Fallback path test. The quick brown fox jumps over the lazy dog. "
               "Second sentence here for padding purposes.\n\n"
               "Second paragraph adds length so two chunks exist for stitching.")
    out = synthesizer.synthesize(text_fb, title="fallback_test")
    dur = None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, timeout=30)
        dur = float(r.stdout.strip())
    except Exception:
        pass
    check("pydub fallback produces valid mp3", out.exists() and dur and dur > 1,
          f"(duration={dur})")
finally:
    synthesizer._stitch_ffmpeg = orig_stitch


# ── Test 2: full worker flow with real DB + throttled progress ─────────────
status_calls = []
orig_set_status = worker._set_status
def counting_set_status(job_id, status, **kw):
    if status == "synthesizing":
        status_calls.append((time.monotonic(), kw.get("progress_pct")))
    return orig_set_status(job_id, status, **kw)
worker._set_status = counting_set_status

text_long = build_sample(1400)  # ~8.5k chars -> ~7-8 chunks at conc 8
ch = ChapterText(
    title="Chapter 12: The Optimized Harbor",
    text=text_long,
    word_count=len(text_long.split()),
    source_url="https://example.com/chapter-12",
    next_chapter_url=None,
    novel_title="Bench Novel",
    source_name="bench",
    chapter_number=12,
)

db = database.SessionLocal()
job_id = "test-job-0001"
db.add(Job(id=job_id, url=ch.source_url, voice="en-US-BrianNeural",
           rate="+0%", status="queued"))
db.commit()
db.close()

t0 = time.perf_counter()
worker.process_pre_extracted_job(job_id, ch)
wall = time.perf_counter() - t0

db = database.SessionLocal()
job = db.query(Job).filter(Job.id == job_id).first()
chapter_row = db.query(Chapter).filter(Chapter.id == job.chapter_id).first() if job else None
audio_path = Path(chapter_row.audio_path) if chapter_row else None
dur = None
if audio_path and audio_path.exists():
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(audio_path)],
                       capture_output=True, text=True, timeout=60)
    try:
        dur = float(r.stdout.strip())
    except ValueError:
        pass
db.close()

check("job completed", bool(job) and job.status == "done", f"(status={getattr(job,'status',None)})")
check("chapter row saved with audio", bool(audio_path and audio_path.exists()),
      f"(path={audio_path})")
check("final mp3 duration plausible", bool(dur and 300 < dur < 1200),
      f"(duration={dur}s, wall={wall:.1f}s)")
pcts = [p for _, p in status_calls]
gaps_ok = all(
    t2 - t1 >= worker.PROGRESS_MIN_INTERVAL - 0.05
    for (t1, p1), (t2, p2) in zip(status_calls, status_calls[1:])
    if p1 not in (0, 100) and p2 not in (0, 100)
)
check("progress writes throttled (>=1s apart mid-run)",
      gaps_ok and len(pcts) >= 2,
      f"(writes={len(pcts)}: {pcts})")
check("title slug strips chapter number",
      bool(audio_path and audio_path.name.startswith("chapter_12_the_optimized")),
      f"({audio_path.name if audio_path else '?'})")

# ── Test 3: cache hit makes identical re-synthesis near-instant ────────────
t1 = time.perf_counter()
out2 = synthesizer.synthesize(text_long, title="cache_hit_test")
warm = time.perf_counter() - t1
check("warm-cache resynthesis fast", warm < 10, f"(wall={warm:.2f}s)")

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)

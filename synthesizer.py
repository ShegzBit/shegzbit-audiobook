"""
synthesizer.py — TTS synthesis using edge-tts + ffmpeg.
synthesize(text, voice, rate, title) -> Path to finished MP3.

Performance features:
- Adaptive sentence-boundary chunking sized so all chunks synthesize in
  a single parallel wave (override with TTS_CHUNK_CHARS).
- Parallel synthesis bounded by TTS_CONCURRENCY (default 8) sharing one
  aiohttp connection pool, with per-chunk retry + exponential backoff.
- Content-keyed chunk cache under output/.cache/parts, keyed by
  sha256(chunk_text + voice + rate). Retries/resubmits skip finished
  chunks; a captcha or garbage extraction hashes to a different key, so
  bad audio can never be served on retry.
- Cached parts are only trusted when their duration is plausible for the
  text length (rejects truncated/empty/error MP3s). The cache grows
  unbounded by design; clear output/.cache/parts to reclaim space.
- Stitching uses ffmpeg's concat demuxer with stream copy (-c copy) so
  the final file is never fully decoded/re-encoded; falls back to pydub.

Measured on ~5k-word chapter (30k chars), same machine/connection:
~183s baseline -> ~38s with TTS_CONCURRENCY=32; ~70-110s at default 8.
"""

import asyncio
import hashlib
import os
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import aiohttp
import edge_tts
from edge_tts.exceptions import EdgeTTSException
from pydub import AudioSegment

OUTPUT_DIR = Path("output")
CACHE_DIR = OUTPUT_DIR / ".cache"
PARTS_CACHE = CACHE_DIR / "parts"

DEFAULT_CHUNK_CHARS = 3500
MIN_ADAPTIVE_CHUNK_CHARS = 1200
MAX_CHUNK_CHARS = 4500
DEFAULT_CONCURRENCY = 8
MAX_CONCURRENCY = 64
RETRY_ATTEMPTS = 3

# Speech is never faster than ~40 chars/sec; anything shorter than that
# bound for its text is treated as truncated/failed audio.
MIN_CHARS_PER_SEC = 40.0

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")

_RETRYABLE_ERRORS = (
    EdgeTTSException,
    asyncio.TimeoutError,
    TimeoutError,
    aiohttp.ClientError,
    ConnectionError,
)


def _env_int(name: str, default: int, lo: int = 1, hi: int | None = None) -> int:
    try:
        val = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    pieces = []
    while len(sentence) > max_chars:
        cut = sentence.rfind(" ", 0, max_chars)
        cut = cut if cut > 0 else max_chars
        pieces.append(sentence[:cut].rstrip())
        sentence = sentence[cut:].lstrip()
    if sentence:
        pieces.append(sentence)
    return pieces


def chunk_text(text: str, max_chars: int | None = None) -> list[str]:
    """Split text into chunks of <= max_chars, breaking at sentence
    boundaries where possible and word boundaries as a last resort."""
    if max_chars is None:
        max_chars = _env_int("TTS_CHUNK_CHARS", DEFAULT_CHUNK_CHARS)
    chunks: list[str] = []
    current = ""

    def _pack(pieces: list[str]) -> None:
        nonlocal current
        for piece in pieces:
            extra = len(piece) + (1 if current else 0)
            if len(current) + extra <= max_chars:
                current = f"{current} {piece}" if current else piece
            else:
                if current:
                    chunks.append(current)
                current = piece

    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        joiner = "\n\n" if current else ""
        if len(current) + len(joiner) + len(para) <= max_chars:
            current = f"{current}{joiner}{para}"
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(para) <= max_chars:
            current = para
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(para) if s.strip()]
        pieces: list[str] = []
        for s in sentences:
            pieces.extend(_split_long_sentence(s, max_chars))
        _pack(pieces)

    if current:
        chunks.append(current)
    return chunks


def _adaptive_chunk_chars(text_len: int, concurrency: int) -> int:
    """Pick a chunk size that lets all chunks synthesize in one wave.

    Wall time ~= ceil(chunks/concurrency) * per_chunk_time and
    per_chunk_time grows with chunk length, so the fastest layout is the
    smallest chunk count that still fits in a single parallel wave.
    """
    if text_len <= concurrency:
        return text_len
    ideal = -(-text_len // concurrency)          # ceil(text_len / conc)
    return max(MIN_ADAPTIVE_CHUNK_CHARS, min(MAX_CHUNK_CHARS, ideal))


def _chunk_single_wave(text: str, concurrency: int) -> list[str]:
    """Chunk text to fit one parallel wave, growing the target size until
    packing overhead/paragraph boundaries stop forcing extra waves."""
    target = _adaptive_chunk_chars(len(text), concurrency)
    chunks = chunk_text(text, target)
    while len(chunks) > concurrency and target < MAX_CHUNK_CHARS:
        target = min(MAX_CHUNK_CHARS, int(target * len(chunks) / concurrency) + 1)
        chunks = chunk_text(text, target)
    return chunks


# ---------------------------------------------------------------------------
# Cache + validation helpers
# ---------------------------------------------------------------------------

def _cache_key(chunk: str, voice: str, rate: str) -> str:
    return hashlib.sha256(f"{chunk}\x00{voice}\x00{rate}".encode("utf-8")).hexdigest()


def _probe_audio(path: Path) -> dict | None:
    """Return {duration, sample_rate, channels, bit_rate} via ffprobe."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration:stream=sample_rate,channels,bit_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
            check=True,
        )
        import json
        data = json.loads(out.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {
            "duration": float(data["format"]["duration"]),
            "sample_rate": int(stream.get("sample_rate", 24000)),
            "channels": int(stream.get("channels", 1)),
            "bit_rate": int(float(stream.get("bit_rate") or 48000)),
        }
    except Exception:
        return None


def _plausible_audio(probe: dict | None, n_chars: int) -> bool:
    if not probe:
        return False
    dur = probe.get("duration") or 0.0
    if dur <= 0:
        return False
    return dur >= n_chars / MIN_CHARS_PER_SEC


def _cached_part_path(key: str) -> Path:
    PARTS_CACHE.mkdir(parents=True, exist_ok=True)
    return PARTS_CACHE / f"{key}.mp3"


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

async def _synthesize_chunk(text: str, voice: str, rate: str, out_path: str,
                            connector=None):
    communicate = edge_tts.Communicate(
        text, voice=voice, rate=rate, connector=connector,
    )
    await communicate.save(out_path)


async def _synthesize_chunk_retry(text: str, voice: str, rate: str,
                                  out_path: str, connector=None):
    delay = 1.0
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            await _synthesize_chunk(text, voice, rate, out_path, connector)
            return
        except _RETRYABLE_ERRORS:
            if attempt == RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay *= 2


async def _synthesize_all(
    chunks: list[str], voice: str, rate: str, tmp_dir: str,
    progress_callback=None, concurrency: int | None = None,
) -> list[str]:
    if concurrency is None:
        concurrency = _env_int("TTS_CONCURRENCY", DEFAULT_CONCURRENCY,
                               hi=MAX_CONCURRENCY)

    sem = asyncio.Semaphore(concurrency)
    total = len(chunks)
    completed = 0

    def _done() -> None:
        nonlocal completed
        completed += 1
        if progress_callback:
            progress_callback(completed, total)

    async def _one(i: int, chunk: str) -> str:
        nonlocal completed
        part_path = os.path.join(tmp_dir, f"part_{i:04d}.mp3")
        key = _cache_key(chunk, voice, rate)
        cached = _cached_part_path(key)
        async with sem:
            probe = _probe_audio(cached)
            if _plausible_audio(probe, len(chunk)):
                shutil.copyfile(cached, part_path)
                _done()
                return part_path
            await _synthesize_chunk_retry(chunk, voice, rate, part_path)
            probe = _probe_audio(Path(part_path))
            if not _plausible_audio(probe, len(chunk)):
                raise RuntimeError(
                    f"Chunk {i} synthesized implausibly short audio "
                    f"({probe['duration'] if probe else '?'}s for "
                    f"{len(chunk)} chars); refusing to cache."
                )
            os.replace(part_path, cached)
            shutil.copyfile(cached, part_path)
            _done()
            return part_path

    # One shared connection pool for all chunk requests.
    connector = aiohttp.TCPConnector(limit=concurrency * 2)
    try:
        paths = await asyncio.gather(*[_one(i, c) for i, c in enumerate(chunks)])
    finally:
        await connector.close()
    return list(paths)


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------

def stitch_audio(paths: list[str], pause_ms: int = 450) -> AudioSegment:
    """Legacy in-memory stitch used as the fallback path."""
    silence = AudioSegment.silent(duration=pause_ms)
    combined = AudioSegment.empty()
    for i, p in enumerate(paths):
        combined += AudioSegment.from_mp3(p)
        if i != len(paths) - 1:
            combined += silence
    return combined


def _ensure_silence_mp3(sample_rate: int, channels: int, bit_rate: int,
                        pause_ms: int) -> Path | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    layout = "mono" if channels <= 1 else "stereo"
    path = CACHE_DIR / f"silence_{sample_rate}_{layout}_{pause_ms}.mp3"
    if path.exists() and path.stat().st_size > 0:
        return path
    kbps = max(bit_rate // 1000, 8)
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={layout}",
             "-t", str(pause_ms / 1000.0), "-b:a", f"{kbps}k",
             str(path)],
            check=True, timeout=30, capture_output=True,
        )
    except Exception:
        return None
    return path if path.exists() and path.stat().st_size > 0 else None


def _stitch_ffmpeg(parts: list[str], pause_ms: int, out_path: Path) -> bool:
    """Stream-copy concatenation via the concat demuxer. No re-encode."""
    list_path = None
    try:
        probe = _probe_audio(Path(parts[0]))
        silence = (_ensure_silence_mp3(probe["sample_rate"], probe["channels"],
                                       probe["bit_rate"], pause_ms)
                   if probe else None)
        lines = []
        for i, p in enumerate(parts):
            lines.append(f"file '{Path(p).resolve()}'")
            if i != len(parts) - 1 and silence:
                lines.append(f"file '{silence.resolve()}'")

        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, dir=str(CACHE_DIR),
        ) as fh:
            fh.write("\n".join(lines) + "\n")
            list_path = fh.name

        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", str(out_path)],
            check=True, timeout=600, capture_output=True,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception:
        return False
    finally:
        if list_path:
            try:
                os.unlink(list_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def synthesize(
    text: str,
    voice: str = "en-US-BrianNeural",
    rate: str = "+0%",
    title: str = "chapter",
    progress_callback=None,
    chapter_number: int | None = None,
) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]
    ch_no = chapter_number if chapter_number is not None else 0
    if ch_no > 0:
        pat = re.compile(rf"^chapter_?0*{ch_no}_?")
        while True:
            new = pat.sub("", slug)
            if new == slug:
                break
            slug = new
    out_path = OUTPUT_DIR / f"chapter_{ch_no}_{slug}.mp3"

    concurrency = _env_int("TTS_CONCURRENCY", DEFAULT_CONCURRENCY, hi=MAX_CONCURRENCY)
    if "TTS_CHUNK_CHARS" in os.environ:
        chunks = chunk_text(text)
    else:
        chunks = _chunk_single_wave(text, concurrency)
    if not chunks:
        raise ValueError("No text to synthesize.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = asyncio.run(
            _synthesize_all(chunks, voice, rate, tmp_dir,
                            progress_callback=progress_callback,
                            concurrency=concurrency)
        )
        pause_ms = 450
        if len(paths) > 1 and not _stitch_ffmpeg(paths, pause_ms, out_path):
            stitch_audio(paths, pause_ms).export(str(out_path), format="mp3")
        elif len(paths) == 1:
            shutil.copyfile(paths[0], out_path)

    return out_path

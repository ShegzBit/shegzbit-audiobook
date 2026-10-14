"""
synthesizer.py — TTS synthesis with pluggable backends (local + edge-tts) + ffmpeg.

synthesize(text, voice, rate, title, backend=None, base_url=None) -> Path to MP3.

Backends:
- "local" (default): POST {base_url}/api/tts {"text","voice","speed"} -> audio/wav.
  Base URL default http://127.0.0.1:5001; other endpoints are appended
  ({base}/api/voices, {base}/health). Concurrency defaults to 4 (server
  allows max 4 concurrent syntheses, 503 on overload) with 503-aware retry.
- "edge": edge-tts Communicate(text, voice, rate).save() -> mp3.
  Concurrency default 8. Kept as fallback via UI toggle.

Performance features (both backends):
- Adaptive sentence-boundary chunking sized so all chunks synthesize in
  a single parallel wave (override with TTS_CHUNK_CHARS).
- Parallel synthesis bounded by TTS_CONCURRENCY sharing one aiohttp
  session, with per-chunk retry + exponential backoff.
- Content-keyed chunk cache: output/.cache/parts (edge .mp3) and
  output/.cache/parts_local (local .wav), keyed by
  sha256(chunk + voice + rate/speed + backend).
- Cached parts trusted only when duration plausible for text length.
- Stitching: edge mp3 parts use concat demuxer stream copy (-c copy);
  local wav parts are concat + libmp3lame re-encode to final mp3.
  Falls back to pydub.
"""

import asyncio
import hashlib
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import aiohttp
from pydub import AudioSegment

try:
    import edge_tts
    from edge_tts.exceptions import EdgeTTSException
    _EDGE_AVAILABLE = True
except Exception:  # edge-tts optional when running local-only
    edge_tts = None  # type: ignore
    EdgeTTSException = Exception  # type: ignore
    _EDGE_AVAILABLE = False

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output")
CACHE_DIR = OUTPUT_DIR / ".cache"
PARTS_CACHE = CACHE_DIR / "parts"
PARTS_LOCAL_CACHE = CACHE_DIR / "parts_local"

DEFAULT_CHUNK_CHARS = 3500         # edge default (large, few calls)
LOCAL_DEFAULT_CHUNK_CHARS = 900    # local default (minimal text per call)
MIN_ADAPTIVE_CHUNK_CHARS = 400
MAX_CHUNK_CHARS = 4500  # hard ceiling, safely under the local server's 5000-char limit
DEFAULT_PAUSE_MS = 180             # inter-chunk silence (override with TTS_PAUSE_MS)
DEFAULT_CONCURRENCY = 8          # edge default
LOCAL_DEFAULT_CONCURRENCY = 4    # server allows max 4 concurrent
MAX_CONCURRENCY = 64
RETRY_ATTEMPTS = 3
LOCAL_RETRY_ATTEMPTS = 4

DEFAULT_TTS_BASE_URL = "http://127.0.0.1:5001"
DEFAULT_BACKEND = "local"
LOCAL_DEFAULT_VOICE = "Bella"
TTS_TIMEOUT_S = 120

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


class LocalTTSError(RuntimeError):
    pass


class LocalTTSOverloaded(LocalTTSError):
    """503/429 from the local server — retryable with backoff."""


class LocalTTSBadRequest(LocalTTSError):
    """400 from the local server — not retryable (bad voice/text/speed)."""


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
# Backend / URL / speed helpers
# ---------------------------------------------------------------------------

def normalize_base_url(url: str | None) -> str:
    """Normalize a user- or env-provided base URL.

    Accepts the bare base (http://127.0.0.1:5001), a trailing slash, or a
    full endpoint URL pasted by mistake (http://127.0.0.1:5001/api/tts);
    always returns the bare base so callers can append endpoints.
    """
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return DEFAULT_TTS_BASE_URL
    # Strip a mistakenly pasted endpoint suffix.
    for suffix in ("/api/tts", "/api/voices", "/health", "/api"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].rstrip("/")
            break
    return raw or DEFAULT_TTS_BASE_URL


def resolve_base_url(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return normalize_base_url(explicit)
    for env_name in ("TTS_BASE_URL", "TTS_ENDPOINT_URL"):
        val = os.environ.get(env_name, "").strip()
        if val:
            return normalize_base_url(val)
    return DEFAULT_TTS_BASE_URL


def resolve_backend(explicit: str | None = None) -> str:
    if explicit and explicit.strip().lower() in ("local", "edge"):
        return explicit.strip().lower()
    env = os.environ.get("TTS_BACKEND", "").strip().lower()
    if env in ("local", "edge"):
        return env
    return DEFAULT_BACKEND


def rate_to_speed(rate: str | float | int | None) -> float:
    """Map legacy '+X%' rate strings (and floats) to local speed 0.5-2.0.

    '+0%' -> 1.0, '+20%' -> 1.2, '-10%' -> 0.9, '1.25'/1.25 -> 1.25.
    Clamped to [0.5, 2.0] per server limits.
    """
    if rate is None or rate == "":
        return 1.0
    if isinstance(rate, (int, float)):
        try:
            return max(0.5, min(2.0, float(rate)))
        except (TypeError, ValueError):
            return 1.0
    s = str(rate).strip().rstrip("%").strip()
    try:
        if s.startswith("+") or s.startswith("-"):
            return max(0.5, min(2.0, 1.0 + float(s) / 100.0))
        return max(0.5, min(2.0, float(s)))
    except ValueError:
        return 1.0


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


def _default_chunk_chars(backend: str) -> int:
    """Per-backend default max chars per chunk.

    Local uses small chunks (minimal text per call); edge keeps the
    large-call behavior. TTS_CHUNK_CHARS env overrides both.
    """
    if "TTS_CHUNK_CHARS" in os.environ:
        return _env_int("TTS_CHUNK_CHARS", DEFAULT_CHUNK_CHARS)
    return LOCAL_DEFAULT_CHUNK_CHARS if backend == "local" else DEFAULT_CHUNK_CHARS


def _chunk_single_wave(text: str, concurrency: int,
                       max_target: int | None = None) -> list[str]:
    """Chunk text to fit one parallel wave, growing the target size until
    packing overhead/paragraph boundaries stop forcing extra waves.

    max_target caps the growth so small-chunk modes (e.g. local ~900
    chars) are never grown back into large chunks; when the adaptive
    target already exceeds max_target the result is fixed-size chunking.
    """
    if max_target is None:
        max_target = MAX_CHUNK_CHARS
    max_target = max(1, min(MAX_CHUNK_CHARS, int(max_target)))
    target = min(_adaptive_chunk_chars(len(text), concurrency), max_target)
    chunks = chunk_text(text, target)
    while len(chunks) > concurrency and target < max_target:
        target = min(max_target, int(target * len(chunks) / concurrency) + 1)
        chunks = chunk_text(text, target)
    return chunks


# ---------------------------------------------------------------------------
# Cache + validation helpers
# ---------------------------------------------------------------------------

def _cache_key(chunk: str, voice: str, rate: str, backend: str = "edge") -> str:
    if backend == "local":
        speed = f"{rate_to_speed(rate):.3f}"
        return hashlib.sha256(
            f"local\x00{chunk}\x00{voice}\x00{speed}".encode("utf-8")
        ).hexdigest()
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


def _cached_part_path(key: str, backend: str = "edge") -> Path:
    if backend == "local":
        PARTS_LOCAL_CACHE.mkdir(parents=True, exist_ok=True)
        return PARTS_LOCAL_CACHE / f"{key}.wav"
    PARTS_CACHE.mkdir(parents=True, exist_ok=True)
    return PARTS_CACHE / f"{key}.mp3"


# ---------------------------------------------------------------------------
# Synthesis — edge backend
# ---------------------------------------------------------------------------

async def _synthesize_chunk_edge(text: str, voice: str, rate: str, out_path: str,
                                 connector=None):
    if not _EDGE_AVAILABLE:
        raise RuntimeError("edge-tts is not installed; use backend='local'.")
    communicate = edge_tts.Communicate(
        text, voice=voice, rate=rate, connector=connector,
    )
    await communicate.save(out_path)


async def _synthesize_chunk_edge_retry(text: str, voice: str, rate: str,
                                       out_path: str, connector=None):
    delay = 1.0
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            await _synthesize_chunk_edge(text, voice, rate, out_path, connector)
            return
        except _RETRYABLE_ERRORS:
            if attempt == RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay *= 2


# ---------------------------------------------------------------------------
# Synthesis — local backend
# ---------------------------------------------------------------------------

async def _synthesize_chunk_local(text: str, voice: str, speed: float,
                                  out_path: str,
                                  base_url: str,
                                  session: aiohttp.ClientSession):
    """Single POST to {base_url}/api/tts. Raises retryable/non-retryable errors."""
    if len(text) > 5000:
        raise LocalTTSBadRequest(f"Chunk too long ({len(text)} chars > 5000).")
    url = f"{base_url.rstrip('/')}/api/tts"
    timeout = aiohttp.ClientTimeout(total=TTS_TIMEOUT_S)
    try:
        async with session.post(
            url, json={"text": text, "voice": voice, "speed": speed},
            timeout=timeout,
        ) as resp:
            if resp.status in (429, 503):
                retry_after = resp.headers.get("Retry-After")
                hint = f" (Retry-After: {retry_after})" if retry_after else ""
                raise LocalTTSOverloaded(f"Server overloaded (HTTP {resp.status}){hint}.")
            if resp.status == 400:
                try:
                    detail = (await resp.text())[:300]
                except Exception:
                    detail = ""
                raise LocalTTSBadRequest(f"Local TTS rejected request (400): {detail}")
            if resp.status != 200:
                try:
                    detail = (await resp.text())[:300]
                except Exception:
                    detail = ""
                raise LocalTTSError(f"Local TTS HTTP {resp.status}: {detail}")
            ctype = resp.headers.get("Content-Type", "")
            data = await resp.read()
            if not data:
                raise LocalTTSError("Local TTS returned empty body.")
            if "audio" not in ctype and "octet-stream" not in ctype and "wav" not in ctype:
                # Some servers return wav with generic types; only hard-fail
                # on obvious JSON error payloads.
                if ctype.startswith("application/json"):
                    raise LocalTTSBadRequest(f"Local TTS error payload: {data[:300]!r}")
            Path(out_path).write_bytes(data)
    except (LocalTTSBadRequest, LocalTTSOverloaded):
        raise
    except (asyncio.TimeoutError, TimeoutError, aiohttp.ClientError) as e:
        raise LocalTTSError(f"Local TTS transport error: {e}") from e


async def _synthesize_chunk_local_retry(text: str, voice: str, speed: float,
                                        out_path: str, base_url: str,
                                        session: aiohttp.ClientSession):
    delay = 1.5
    for attempt in range(1, LOCAL_RETRY_ATTEMPTS + 1):
        try:
            await _synthesize_chunk_local(text, voice, speed, out_path,
                                          base_url, session)
            return
        except LocalTTSBadRequest:
            raise  # never retry 400s
        except (LocalTTSOverloaded, LocalTTSError,
                asyncio.TimeoutError, TimeoutError,
                aiohttp.ClientError, ConnectionError) as e:
            if attempt == LOCAL_RETRY_ATTEMPTS:
                raise
            logger.warning("Local TTS chunk failed (attempt %d/%d): %s",
                           attempt, LOCAL_RETRY_ATTEMPTS, e)
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay *= 2


async def _synthesize_all(
    chunks: list[str], voice: str, rate: str, tmp_dir: str,
    progress_callback=None, concurrency: int | None = None,
    backend: str = "edge", base_url: str | None = None,
) -> list[str]:
    backend = resolve_backend(backend)
    base = resolve_base_url(base_url) if backend == "local" else ""
    if concurrency is None:
        if backend == "local":
            # Server caps at 4 concurrent syntheses — clamp to avoid
            # guaranteed 503s even if TTS_CONCURRENCY is set high for edge.
            concurrency = _env_int("TTS_CONCURRENCY", LOCAL_DEFAULT_CONCURRENCY,
                                   hi=LOCAL_DEFAULT_CONCURRENCY)
        else:
            concurrency = _env_int("TTS_CONCURRENCY", DEFAULT_CONCURRENCY,
                                   hi=MAX_CONCURRENCY)
    else:
        if backend == "local":
            concurrency = max(1, min(int(concurrency), LOCAL_DEFAULT_CONCURRENCY))
        else:
            concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))

    sem = asyncio.Semaphore(concurrency)
    total = len(chunks)
    completed = 0

    def _done() -> None:
        nonlocal completed
        completed += 1
        if progress_callback:
            progress_callback(completed, total)

    speed = rate_to_speed(rate) if backend == "local" else 0.0
    ext = "wav" if backend == "local" else "mp3"

    async def _one(i: int, chunk: str, session=None) -> str:
        part_path = os.path.join(tmp_dir, f"part_{i:04d}.{ext}")
        key = _cache_key(chunk, voice, rate, backend)
        cached = _cached_part_path(key, backend)
        async with sem:
            probe = _probe_audio(cached)
            if cached.exists() and _plausible_audio(probe, len(chunk)):
                shutil.copyfile(cached, part_path)
                _done()
                return part_path
            if backend == "local":
                assert session is not None
                await _synthesize_chunk_local_retry(chunk, voice, speed,
                                                    part_path, base, session)
            else:
                await _synthesize_chunk_edge_retry(chunk, voice, rate, part_path)
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

    if backend == "local":
        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        async with aiohttp.ClientSession(connector=connector) as session:
            paths = await asyncio.gather(
                *[_one(i, c, session) for i, c in enumerate(chunks)]
            )
        return list(paths)

    # Edge path: one shared connection pool for all chunk requests.
    connector = aiohttp.TCPConnector(limit=concurrency * 2)
    try:
        paths = await asyncio.gather(*[_one(i, c) for i, c in enumerate(chunks)])
    finally:
        await connector.close()
    return list(paths)


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------

def _load_part(path: str) -> AudioSegment:
    p = Path(path)
    if p.suffix.lower() == ".wav":
        return AudioSegment.from_wav(p)
    return AudioSegment.from_mp3(p)


def stitch_audio(paths: list[str], pause_ms: int = 450) -> AudioSegment:
    """Legacy in-memory stitch used as the fallback path."""
    silence = AudioSegment.silent(duration=pause_ms)
    combined = AudioSegment.empty()
    for i, p in enumerate(paths):
        combined += _load_part(p)
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


def _ensure_silence_wav(sample_rate: int, channels: int,
                        pause_ms: int) -> Path | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    layout = "mono" if channels <= 1 else "stereo"
    path = CACHE_DIR / f"silence_{sample_rate}_{layout}_{pause_ms}.wav"
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={layout}",
             "-t", str(pause_ms / 1000.0),
             str(path)],
            check=True, timeout=30, capture_output=True,
        )
    except Exception:
        return None
    return path if path.exists() and path.stat().st_size > 0 else None


def _stitch_ffmpeg(parts: list[str], pause_ms: int, out_path: Path) -> bool:
    """Concat via the concat demuxer.

    MP3 parts: stream copy (-c copy), no re-encode.
    WAV parts (local backend): re-encode to MP3 via libmp3lame.
    """
    list_path = None
    try:
        is_wav = Path(parts[0]).suffix.lower() == ".wav"
        probe = _probe_audio(Path(parts[0]))
        if is_wav:
            silence = (_ensure_silence_wav(probe["sample_rate"], probe["channels"],
                                           pause_ms)
                       if probe else None)
        else:
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

        if is_wav:
            cmd = ["ffmpeg", "-v", "error", "-y",
                   "-f", "concat", "-safe", "0", "-i", list_path,
                   "-c:a", "libmp3lame", "-b:a", "128k", str(out_path)]
        else:
            cmd = ["ffmpeg", "-v", "error", "-y",
                   "-f", "concat", "-safe", "0", "-i", list_path,
                   "-c", "copy", str(out_path)]
        subprocess.run(cmd, check=True, timeout=600, capture_output=True)
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
# Sync helpers for the web layer (voices / health)
# ---------------------------------------------------------------------------

_voices_cache: dict[str, tuple[float, list]] = {}
_VOICES_TTL = 60.0


def _parse_voices_payload(data) -> list:
    """Accept the shapes a local server might return and normalize to
    [{"id":..., "label":...}]."""
    if isinstance(data, dict):
        for key in ("voices", "data", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, str):
            out.append({"id": item, "label": item})
        elif isinstance(item, dict):
            vid = (item.get("id") or item.get("name") or item.get("voice")
                   or item.get("short_name") or "")
            label = (item.get("label") or item.get("display_name") or vid)
            if vid:
                out.append({"id": str(vid), "label": str(label)})
    return out


def fetch_local_voices(base_url: str | None = None, timeout: float = 10.0) -> list:
    """Synchronous fetch of {base}/api/voices with short TTL cache."""
    import json as _json
    import urllib.request
    base = resolve_base_url(base_url)
    now = time.monotonic()
    hit = _voices_cache.get(base)
    if hit and now - hit[0] < _VOICES_TTL:
        return hit[1]
    url = f"{base.rstrip('/')}/api/voices"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode("utf-8") or "null")
        voices = _parse_voices_payload(payload)
        if voices:
            _voices_cache[base] = (now, voices)
        return voices
    except Exception as e:
        logger.warning("fetch_local_voices(%s) failed: %s", base, e)
        if hit:
            return hit[1]
        return []


def check_local_health(base_url: str | None = None, timeout: float = 5.0) -> dict:
    """Synchronous GET {base}/health -> {"ok": bool, ...}."""
    import json as _json
    import urllib.request
    base = resolve_base_url(base_url)
    url = f"{base.rstrip('/')}/health"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")[:2000]
            try:
                payload = _json.loads(body or "{}")
            except Exception:
                payload = {"raw": body}
            return {"ok": 200 <= resp.status < 300, "status": resp.status,
                    "base_url": base, "payload": payload}
    except Exception as e:
        return {"ok": False, "base_url": base, "error": str(e)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _require_ffmpeg() -> None:
    """Fail fast with an actionable message when ffmpeg/ffprobe are missing.

    Both are required: ffprobe validates chunk audio, ffmpeg stitches parts,
    and the pydub fallback also shells out to ffmpeg. Without this check a
    missing binary surfaces later as a confusing "implausibly short audio"
    RuntimeError.
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    raise RuntimeError(
        "ffmpeg and ffprobe are required but were not found on PATH. "
        "Install them and re-run: "
        "Windows (winget): 'winget install Gyan.FFmpeg' then open a new terminal; "
        "Debian/Ubuntu: 'sudo apt install ffmpeg'; "
        "macOS: 'brew install ffmpeg'."
    )


def synthesize(
    text: str,
    voice: str = "en-US-BrianNeural",
    rate: str = "+0%",
    title: str = "chapter",
    progress_callback=None,
    chapter_number: int | None = None,
    backend: str | None = None,
    base_url: str | None = None,
    concurrency: int | None = None,
) -> Path:
    _require_ffmpeg()
    backend = resolve_backend(backend)
    base = resolve_base_url(base_url) if backend == "local" else ""
    if backend == "local" and (not voice or not voice.strip()):
        voice = LOCAL_DEFAULT_VOICE

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

    if concurrency is None:
        if backend == "local":
            concurrency = _env_int("TTS_CONCURRENCY", LOCAL_DEFAULT_CONCURRENCY,
                                   hi=LOCAL_DEFAULT_CONCURRENCY)
        else:
            concurrency = _env_int("TTS_CONCURRENCY", DEFAULT_CONCURRENCY, hi=MAX_CONCURRENCY)
    else:
        try:
            concurrency = int(concurrency)
        except (TypeError, ValueError):
            concurrency = (LOCAL_DEFAULT_CONCURRENCY if backend == "local"
                           else DEFAULT_CONCURRENCY)
        if backend == "local":
            concurrency = max(1, min(concurrency, LOCAL_DEFAULT_CONCURRENCY))
        else:
            concurrency = max(1, min(concurrency, MAX_CONCURRENCY))
    if "TTS_CHUNK_CHARS" in os.environ:
        chunks = chunk_text(text)
    else:
        chunks = _chunk_single_wave(text, concurrency,
                                    max_target=_default_chunk_chars(backend))
    if not chunks:
        raise ValueError("No text to synthesize.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = asyncio.run(
            _synthesize_all(chunks, voice, rate, tmp_dir,
                            progress_callback=progress_callback,
                            concurrency=concurrency,
                            backend=backend, base_url=base)
        )
        pause_ms = _env_int("TTS_PAUSE_MS", DEFAULT_PAUSE_MS, lo=0, hi=5000)
        if len(paths) > 1 and not _stitch_ffmpeg(paths, pause_ms, out_path):
            stitch_audio(paths, pause_ms).export(str(out_path), format="mp3")
        elif len(paths) == 1:
            # Single local wav part still needs wav->mp3 conversion.
            if Path(paths[0]).suffix.lower() == ".wav":
                if not _stitch_ffmpeg(paths, 0, out_path):
                    _load_part(paths[0]).export(str(out_path), format="mp3")
            else:
                shutil.copyfile(paths[0], out_path)

    return out_path

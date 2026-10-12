# Web Novel TTS

A family-use web app that converts web novel chapters to natural-sounding audiobooks.

## Features

- **Paste any chapter URL** → extracts clean text → synthesises MP3 with edge-tts
- **Background jobs** with real-time status polling (no page freezes during synthesis)
- **Caching** — same chapter/voice/rate combo returns instantly on re-request
- **Library** — tracks novels, chapters, and listening progress automatically
- **Auto-advance** — queues the next chapter when the current one finishes
- **Captcha handling** — detects blocked pages and guides you to solve them manually
- **Persistent player** — speed control (0.75×–2×), skip ±10s, auto-saves position
- **RSS feed** — each novel gets `/rss/{id}.xml` to subscribe in a podcast app
- **Auth** — optional shared password (set `SHARED_ACCESS_PASSWORD` env var)

## Stack

- **Backend**: FastAPI + SQLite (SQLAlchemy)
- **TTS**: edge-tts + pydub (free neural voices, no API key)
- **Text extraction**: site-specific CSS selectors → trafilatura → BeautifulSoup heuristic
- **Job queue**: ThreadPoolExecutor (no Redis needed)

## Environment variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `SHARED_ACCESS_PASSWORD` | Shared login password for all family members | No (auth disabled if unset) |
| `SECRET_KEY` | Cookie signing key (auto-generated if unset) | No |
| `TTS_BACKEND` | Default TTS engine: `local` or `edge` (per-job toggle in UI overrides) | No (default `local`) |
| `TTS_BASE_URL` | Local TTS base URL; `/api/tts`, `/api/voices`, `/health` are appended (default `http://127.0.0.1:5001`) | No |
| `TTS_CONCURRENCY` | Parallel chunk syntheses (local is capped at 2 — the server 503s above that; edge default 8) | No |
| `TTS_CHUNK_CHARS` | Max chars per TTS call, overriding the per-backend default (local 900, edge 3500; hard ceiling 4500) | No |
| `TTS_PAUSE_MS` | Silence stitched between chunks in ms (default 180) | No |

## Running

```bash
python main.py
```
Serves on port 5000.

### Running on Windows (source install, edge-tts backend)

No code changes needed. The local TTS server (`127.0.0.1:5001`) is not
required — use `TTS_BACKEND=edge` so synthesis goes through edge-tts.

```powershell
# One-time setup (from the project root):
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1

# Or manually:
winget install Python.Python.3.12 Gyan.FFmpeg
py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium

# Every run (open a NEW terminal after installing FFmpeg so PATH refreshes):
$env:TTS_BACKEND="edge"; .\.venv\Scripts\python.exe main.py
# Open http://localhost:5000 (allow the Windows firewall prompt)
```

Requirements on Windows: Python 3.11+, `ffmpeg` + `ffprobe` on `PATH`
(`synthesize()` fails fast with install instructions if missing), and
Playwright Chromium (`python -m playwright install chromium`).

## User preferences

- Dark theme, minimal UI
- No external API keys required for TTS
- Optimize for small group (2-5 users), not public scale

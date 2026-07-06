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

## Running

```bash
python main.py
```
Serves on port 5000.

## User preferences

- Dark theme, minimal UI
- No external API keys required for TTS
- Optimize for small group (2-5 users), not public scale

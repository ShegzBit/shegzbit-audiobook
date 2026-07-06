#!/usr/bin/env python3
"""
Web Novel TTS — Flask web app wrapping novel_reader logic.
Fetches a chapter URL, extracts text, synthesises with edge-tts,
returns an MP3 for download.
"""

import asyncio
import io
import os
import re
import tempfile

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, send_file

try:
    import trafilatura
except ImportError:
    trafilatura = None

import edge_tts
from pydub import AudioSegment

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

VOICES = {
    "en-US-BrianNeural":  "Brian (US, Male, Warm)",
    "en-US-AndrewNeural": "Andrew (US, Male)",
    "en-US-EmmaNeural":   "Emma (US, Female, Warm)",
    "en-US-AvaNeural":    "Ava (US, Female)",
    "en-GB-RyanNeural":   "Ryan (UK, Male)",
    "en-GB-SoniaNeural":  "Sonia (UK, Female)",
    "en-AU-WilliamNeural":"William (AU, Male)",
    "en-AU-NatashaNeural":"Natasha (AU, Female)",
}

NOISE_SELECTORS = [
    "script", "style", "nav", "header", "footer", "aside", "form",
    "iframe", "noscript", "button", "svg",
]

NOISE_PATTERNS = [
    r"advertisement", r"report chapter", r"next chapter", r"previous chapter",
    r"read more chapters?.*", r"bookmark this page", r"^\s*chapter list\s*$",
    r"please disable your ad.?blocker", r"if you.*enjoy.*support.*author",
    r"^default$", r"^dyslexic$", r"^roboto$", r"^lora$",
    r"^prev$", r"^next$", r"^index$", r"chevron_left", r"chevron_right",
    r"^nights_stay$", r"^home$",
    r"tap the screen to use advanced tools",
    r"you can use left and right keyboard keys",
    r"^search$", r"^categories$", r"^tags$", r"^updates$",
    r"your (fictional|fan-fiction) stories hub",
]

TRAILING_CUTOFFS = [
    r"you'?ll also like",
    r"^\s*###?\s*user comments",
    r"^\s*###?\s*hot keywords",
]

SITE_TEMPLATE_SELECTORS = [
    "#chapter-content", ".chapter-content", ".chapter-c",
    "#chr-content", ".chr-c", ".txt", "#content-area",
    "article .content", ".reading-content",
]


def fetch_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def extract_with_known_selectors(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(NOISE_SELECTORS):
        tag.decompose()
    for selector in SITE_TEMPLATE_SELECTORS:
        node = soup.select_one(selector)
        if node:
            paragraphs = node.find_all("p")
            if paragraphs:
                text = "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs)
            else:
                text = node.get_text("\n\n", strip=True)
            if len(text) > 200:
                return text
    return None


def extract_with_trafilatura(html: str):
    if trafilatura is None:
        return None
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    return text


def extract_with_bs4_heuristic(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(NOISE_SELECTORS):
        tag.decompose()
    candidates = soup.find_all(["div", "article", "section"])
    best, best_len = None, 0
    for c in candidates:
        text = c.get_text(" ", strip=True)
        if len(text) > best_len:
            best, best_len = c, len(text)
    if best is None:
        return soup.get_text(" ", strip=True)
    paragraphs = best.find_all("p")
    if paragraphs:
        return "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs)
    return best.get_text("\n\n", strip=True)


def clean_text(raw: str) -> str:
    lines = [ln.strip() for ln in raw.splitlines()]
    cleaned = []
    for ln in lines:
        if not ln:
            continue
        low = ln.lower()
        if any(re.search(pat, low) for pat in TRAILING_CUTOFFS):
            break
        if any(re.search(pat, low) for pat in NOISE_PATTERNS):
            continue
        if len(ln) < 3:
            continue
        cleaned.append(ln)
    text = "\n\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_chapter_title(html: str):
    soup = BeautifulSoup(html, "html.parser")
    h2 = soup.find("h2")
    if h2 and h2.get_text(strip=True):
        return h2.get_text(strip=True)
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        return title_tag.get_text(strip=True).split("|")[-1].strip()
    return None


def get_chapter_text(url: str):
    html = fetch_html(url)
    title = extract_chapter_title(html)
    text = extract_with_known_selectors(html)
    if not text:
        text = extract_with_trafilatura(html)
    if not text or len(text) < 200:
        text = extract_with_bs4_heuristic(html)
    text = clean_text(text)
    if len(text) < 100:
        raise RuntimeError(
            "Couldn't find a substantial block of chapter text. "
            "The site's layout may need a custom selector."
        )
    return text, title


def chunk_text(text: str, max_chars: int = 1400):
    paragraphs = text.split("\n\n")
    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


async def synthesize_chunk(text: str, voice: str, rate: str, out_path: str):
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(out_path)


async def synthesize_all(chunks, voice, rate, tmp_dir):
    paths = []
    for i, chunk in enumerate(chunks):
        out_path = os.path.join(tmp_dir, f"part_{i:04d}.mp3")
        await synthesize_chunk(chunk, voice, rate, out_path)
        paths.append(out_path)
    return paths


def stitch_audio(paths, pause_ms=450):
    silence = AudioSegment.silent(duration=pause_ms)
    combined = AudioSegment.empty()
    for i, p in enumerate(paths):
        combined += AudioSegment.from_mp3(p)
        if i != len(paths) - 1:
            combined += silence
    return combined


@app.route("/")
def index():
    return render_template("index.html", voices=VOICES)


@app.route("/preview", methods=["POST"])
def preview():
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required."}), 400
    try:
        text, title = get_chapter_text(url)
        word_count = len(text.split())
        preview_text = text[:600] + ("..." if len(text) > 600 else "")
        return jsonify({
            "title": title or "Untitled Chapter",
            "word_count": word_count,
            "preview": preview_text,
            "full_text": text,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/synthesize", methods=["POST"])
def synthesize():
    data = request.json
    url = data.get("url", "").strip()
    voice = data.get("voice", "en-US-BrianNeural")
    rate = data.get("rate", "+0%")
    title_override = data.get("title", "")

    if voice not in VOICES:
        voice = "en-US-BrianNeural"

    if not url:
        return jsonify({"error": "URL is required."}), 400

    try:
        text, title = get_chapter_text(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    chunks = chunk_text(text)

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = asyncio.run(synthesize_all(chunks, voice, rate, tmp_dir))
            audio = stitch_audio(paths)
            buf = io.BytesIO()
            audio.export(buf, format="mp3")
            buf.seek(0)

        used_title = title_override or title or "chapter"
        slug = re.sub(r"[^a-z0-9]+", "_", used_title.lower()).strip("_")[:60]
        filename = f"{slug}.mp3"

        return send_file(
            buf,
            mimetype="audio/mpeg",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({"error": f"TTS synthesis failed: {e}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

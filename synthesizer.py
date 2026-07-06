"""
synthesizer.py — TTS synthesis using edge-tts + pydub.
synthesize(text, voice, rate, title) -> Path to finished MP3.
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path

import edge_tts
from pydub import AudioSegment

OUTPUT_DIR = Path("output")


def chunk_text(text: str, max_chars: int = 1400) -> list[str]:
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


async def _synthesize_chunk(text: str, voice: str, rate: str, out_path: str):
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(out_path)


async def _synthesize_all(chunks: list[str], voice: str, rate: str, tmp_dir: str) -> list[str]:
    paths = []
    for i, chunk in enumerate(chunks):
        out_path = os.path.join(tmp_dir, f"part_{i:04d}.mp3")
        await _synthesize_chunk(chunk, voice, rate, out_path)
        paths.append(out_path)
    return paths


def stitch_audio(paths: list[str], pause_ms: int = 450) -> AudioSegment:
    silence = AudioSegment.silent(duration=pause_ms)
    combined = AudioSegment.empty()
    for i, p in enumerate(paths):
        combined += AudioSegment.from_mp3(p)
        if i != len(paths) - 1:
            combined += silence
    return combined


def synthesize(
    text: str,
    voice: str = "en-US-BrianNeural",
    rate: str = "+0%",
    title: str = "chapter",
) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]
    # Unique-ify by voice+rate suffix to avoid collisions
    voice_slug = re.sub(r"[^a-z0-9]", "", voice.lower())[:12]
    rate_slug = re.sub(r"[^a-z0-9]", "", rate)
    out_path = OUTPUT_DIR / f"{slug}_{voice_slug}_{rate_slug}.mp3"

    chunks = chunk_text(text)

    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = asyncio.run(_synthesize_all(chunks, voice, rate, tmp_dir))
        audio = stitch_audio(paths)
        audio.export(str(out_path), format="mp3")

    return out_path

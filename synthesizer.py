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


async def _synthesize_all(
    chunks: list[str], voice: str, rate: str, tmp_dir: str,
    progress_callback=None, concurrency: int = 4,
) -> list[str]:
    sem = asyncio.Semaphore(concurrency)
    total = len(chunks)
    completed = 0

    async def _one(i: int, chunk: str) -> str:
        nonlocal completed
        async with sem:
            out_path = os.path.join(tmp_dir, f"part_{i:04d}.mp3")
            await _synthesize_chunk(chunk, voice, rate, out_path)
            completed += 1
            if progress_callback:
                progress_callback(completed, total)
            return out_path

    paths = await asyncio.gather(*[_one(i, c) for i, c in enumerate(chunks)])
    return list(paths)


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

    chunks = chunk_text(text)

    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = asyncio.run(
            _synthesize_all(chunks, voice, rate, tmp_dir,
                            progress_callback=progress_callback)
        )
        audio = stitch_audio(paths)
        audio.export(str(out_path), format="mp3")

    return out_path

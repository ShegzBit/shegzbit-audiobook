"""
bench_synthesis.py — Benchmark for the synthesis pipeline.

Generates a deterministic sample chapter, runs it through synthesizer.synthesize()
and reports wall-clock timings broken down by phase (where instrumentable),
plus output audio duration and realtime factor.

Works against any version of synthesizer.py exposing chunk_text/synthesize/
stitch_audio. Run before and after optimization work to measure gains.

Usage:
    python bench_synthesis.py [--words 5000] [--runs 1] [--voice V] [--rate R]
                              [--keep] [--json]

Environment (honoured by newer synthesizer versions):
    TTS_CONCURRENCY   parallel syntheses (local capped at 2, edge default 8)
    TTS_CHUNK_CHARS   max characters per chunk (local default 900, edge 3500)
    TTS_PAUSE_MS      silence stitched between chunks in ms (default 180)
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

OUTPUT_DIR = Path("output")

_WORDS = (
    "the ship sailed north through calm waters while the crew argued about "
    "treasure maps and old legends whispered below deck among sailors who "
    "never learned to read charts properly because their captain preferred "
    "stories over navigation and every morning began with salted meat hard "
    "biscuit and a cup of bitter tea brewed on a small iron stove near the "
    "mast where the lookout watched for sails on the horizon line beyond "
    "the reef birds circled lazily waiting for fish driven up by dolphins "
    "playing in the wake of the vessel as it cut steadily toward open sea "
    "when night fell stars appeared in patterns the navigator knew well "
    "though he never shared his knowledge freely preferring instead to let "
    "others believe he guessed course corrections from intuition alone but "
    "truth was simpler mathematics tables and years of practice logged in a "
    "worn leather journal hidden beneath his bunk alongside letters from home "
    "storm clouds gathered on the third day turning skies an angry shade of "
    "green while waves rose higher than anyone aboard had seen in months of "
    "travel across these waters everyone worked without speaking hauling lines "
    "securing cargo and praying quietly until dawn broke clean and golden over "
    "a sea suddenly calm again as if nothing had happened except torn canvas "
    "and a broken spar drifting somewhere astern evidence of nature brief anger "
    "they repaired what could be repaired buried what could not and continued "
    "westward following currents that promised warmer climates richer ports "
    "and markets crowded with merchants selling spices silk and rumours of "
    "kingdoms where gold flowed like river water during spring floods every "
    "port told similar stories differing only in names distances and the price "
    "of passage toward destinations nobody had ever reached twice by same road "
)

_SENT_END = ". "


def build_sample(target_words: int, seed: int = 42) -> str:
    rng = random.Random(seed)
    words: list[str] = []
    while len(words) < target_words:
        n = rng.randint(8, 20)
        sentence = [rng.choice(_WORDS.split()) for _ in range(n)]
        words.extend(sentence)
        words[-1] += _SENT_END.strip()
        if rng.random() < 0.25:
            words.append("\n\n")
    text = " ".join(w for w in words)
    return "\n\n".join(p.strip() for p in text.split("\n\n") if p.strip())


def audio_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--words", type=int, default=5000)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--voice", default=None)
    ap.add_argument("--rate", default="+0%")
    ap.add_argument("--backend", default=None,
                    help="tts backend: local (default) or edge")
    ap.add_argument("--base-url", default=None,
                    help="local TTS base URL (default http://127.0.0.1:5001)")
    ap.add_argument("--title", default="bench")
    ap.add_argument("--keep", action="store_true", help="keep output files")
    ap.add_argument("--json", action="store_true", help="print machine-readable summary")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    import synthesizer

    backend = synthesizer.resolve_backend(args.backend)
    if args.voice:
        voice = args.voice
    else:
        voice = (synthesizer.LOCAL_DEFAULT_VOICE if backend == "local"
                 else "en-US-BrianNeural")
    base_url = synthesizer.resolve_base_url(args.base_url)
    print(f"backend: {backend} | base: {base_url} | voice: {voice}")

    # Instrument stable entry points so phase times survive refactors.
    phase_times: dict[str, float] = {}

    def _wrap(name: str):
        fn = getattr(synthesizer, name, None)
        if fn is None:
            return
        def inner(*a, **k):
            t0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                phase_times[name] = phase_times.get(name, 0.0) + time.perf_counter() - t0
        setattr(synthesizer, name, inner)

    for name in ("chunk_text", "stitch_audio", "_stitch_ffmpeg"):
        _wrap(name)

    text = build_sample(args.words)
    n_chars = len(text)
    n_words = len(text.split())
    chunks = synthesizer.chunk_text(text)
    n_chunks = len(chunks)
    max_chunk = max(len(c) for c in chunks)
    if hasattr(synthesizer, "_chunk_single_wave"):
        conc = int(os.environ.get("TTS_CONCURRENCY", "") or
                   getattr(synthesizer, "DEFAULT_CONCURRENCY", 8))
        chunks = synthesizer._chunk_single_wave(text, conc)
        n_chunks, max_chunk = len(chunks), max(len(c) for c in chunks)

    print(f"sample: {n_words} words | {n_chars} chars | {n_chunks} chunks "
          f"(largest {max_chunk} chars)")
    if "TTS_CONCURRENCY" in os.environ or "TTS_CHUNK_CHARS" in os.environ:
        print(f"env: TTS_CONCURRENCY={os.environ.get('TTS_CONCURRENCY', '-')} "
              f"TTS_CHUNK_CHARS={os.environ.get('TTS_CHUNK_CHARS', '-')}")
    print(f"running {args.runs} run(s)...\n")

    results = []
    outputs: list[Path] = []
    for run in range(1, args.runs + 1):
        phase_times.clear()
        cb_last = [0.0]

        def progress(done: int, total: int, _run=run):
            now = time.perf_counter()
            if now - cb_last[0] > 0.5 or done == total:
                cb_last[0] = now
                print(f"  run {_run}: chunk {done}/{total}", flush=True)

        t0 = time.perf_counter()
        out_path = synthesizer.synthesize(
            text, voice=voice, rate=args.rate,
            title=f"{args.title}_{run}", progress_callback=progress,
            backend=backend, base_url=base_url,
        )
        wall = time.perf_counter() - t0
        outputs.append(out_path)

        dur = audio_duration(out_path)
        rt = (dur / wall) if dur else None
        stitch_t = phase_times.get("_stitch_ffmpeg") or phase_times.get("stitch_audio")
        results.append({
            "run": run,
            "wall_s": round(wall, 2),
            "stitch_s": round(stitch_t, 2) if stitch_t is not None else None,
            "audio_duration_s": round(dur, 1) if dur else None,
            "realtime_factor": round(rt, 1) if rt else None,
            "output": str(out_path),
        })
        r = results[-1]
        extra = f" stitch={r['stitch_s']}s" if r["stitch_s"] is not None else ""
        print(f"  => wall={r['wall_s']}s{extra} "
              f"audio={r['audio_duration_s']}s factor={r['realtime_factor']}x\n")

    total_wall = sum(r["wall_s"] for r in results)
    print(f"TOTAL: {total_wall}s wall across {args.runs} run(s)")

    if args.json:
        print(json.dumps({
            "words": n_words, "chars": n_chars, "chunks": n_chunks,
            "tts_concurrency_env": os.environ.get("TTS_CONCURRENCY"),
            "chunk_chars_env": os.environ.get("TTS_CHUNK_CHARS"),
            "results": results,
        }, indent=2))

    if not args.keep:
        for p in outputs:
            p.unlink(missing_ok=True)
        print("(outputs cleaned up)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

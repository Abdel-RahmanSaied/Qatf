"""Run one stage-2 transcription with a named decode override, inside Docker.

    python tests/sweep_asr.py <run-name> '<json overrides>'

One variable per run, written to a distinctly named file so runs compare side by
side. This is the driver for the sweep table in docs/quality.md; it deliberately
does NOT touch the transcript cache key, so the offline reference transcript that
every other measurement is anchored to stays valid.

Runs inside the qatf container because that is where the GPU and faster-whisper
are. The host has neither.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

from qatf.core.types import words_to_dicts  # noqa: E402
from qatf.pipeline import asr  # noqa: E402

WAV = Path("/media/audio-denoised.wav")
OUT = Path("/data/sweep")
VOCAB = " ".join(Path("/app/prompts/ar-tech.txt").read_text(encoding="utf-8").split())


def main() -> int:
    name = sys.argv[1]
    overrides = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
    vocab = VOCAB if "--no-vocab" not in sys.argv else None

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"words-{name}.json"

    started = time.time()
    transcript = asr.transcribe(
        WAV, "large-v3", "cuda", "ar",
        initial_prompt=None, hotwords=vocab, decode=overrides or None,
    )
    elapsed = time.time() - started

    target.write_text(json.dumps({
        "language": transcript.language,
        "language_probability": transcript.language_probability,
        "device": transcript.device,
        "compute_type": transcript.compute_type,
        "words": words_to_dicts(transcript.words),
    }, ensure_ascii=False), encoding="utf-8")

    print(f"{name}: {len(transcript.words)} words in {elapsed:.0f}s -> {target}")
    print(f"  decode overrides: {json.dumps(overrides, ensure_ascii=False)}")
    print(f"  vocabulary: {'on' if vocab else 'OFF'} ({len(vocab.split()) if vocab else 0} terms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

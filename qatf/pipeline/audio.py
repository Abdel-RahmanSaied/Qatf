"""Stage 1 — demux audio. ffmpeg only, no model."""

from __future__ import annotations

from pathlib import Path

from ..core.utils import run

#: Speech-band limit plus FFT denoise. Measured on 12 minutes recorded in a
#: moving car: transcription errors 15 -> 11, and ~20% FASTER, because a cleaner
#: signal makes the decoder fall back to higher temperatures less often.
#: Deliberately no `dynaudnorm` — level normalisation was tested and made things
#: markedly worse (correct terms 24 -> 13).
DENOISE_FILTER = "highpass=f=80,lowpass=f=7500,afftdn=nr=12:nf=-25"


def audio_path(work: Path, denoise: bool = False) -> Path:
    """Denoised audio is a different file, not a different flag on the same one —
    otherwise toggling `--denoise` silently reuses whichever wav exists."""
    return work / ("audio-denoised.wav" if denoise else "audio.wav")


def extract_audio(video: Path, out_wav: Path, force: bool = False,
                  denoise: bool = False) -> Path:
    """Demux to 16k mono wav, which is what Whisper wants.

    Skipped when the wav is already there. The transcript cache exists to make
    clip-selection iteration free, and a full ffmpeg pass on every run defeats
    that."""
    if out_wav.exists() and out_wav.stat().st_size > 0 and not force:
        return out_wav
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(video), "-vn"]
    if denoise:
        cmd += ["-af", DENOISE_FILTER]
    cmd += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)]
    run(cmd)
    return out_wav

"""The command-line surface. Nothing here executes anything."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..core.constants import CAPTION_MAX_WORDS, DEFAULT_TRACK_TIER, TRACK_TIERS
from ..pipeline import DEVICES, REFRAME_MODES
from ..pipeline.encode import CODECS, DEFAULT_CODEC, DEFAULT_PRESET, PRESETS


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="qatf",
        description="qatf — harvest the parts of a long video worth watching.",
    )
    # str, NOT Path. `Path("https://youtu.be/x")` silently collapses the double
    # slash to `https:/youtu.be/x`, so converting back to a string hands the
    # fetcher a URL that no longer parses. `runner` decides which of the two
    # this is via `pipeline.is_url`.
    ap.add_argument("video", type=str,
                    help="path to a video file, or an https URL to fetch "
                         "(YouTube only — see --transcript-source)")
    ap.add_argument("-o", "--out", type=Path, default=Path("shorts"))
    ap.add_argument("--clips", type=int, default=5)
    ap.add_argument("--min-len", type=int, default=30)
    ap.add_argument("--max-len", type=int, default=75)
    ap.add_argument("--reframe", choices=list(REFRAME_MODES), default="crop",
                    help="crop fills the 9:16 frame from the centre; blur fits "
                         "the whole frame over a blurred fill; track follows the "
                         "subject. On a 16:9 source crop keeps ~3x the subject "
                         "pixels AND renders ~1.8x faster — blur is slower and "
                         "softer, so prefer crop unless the framing genuinely "
                         "needs the full width. track is crop with a moving "
                         "window and needs OpenCV: pip install -e \".[track]\"")
    ap.add_argument("--track-tier", choices=list(TRACK_TIERS),
                    default=DEFAULT_TRACK_TIER,
                    help="how hard --reframe track looks. fast is 1fps face "
                         "detection; balanced is 3fps; best is 8fps. Never "
                         "silently downgraded — asking for a tier this host "
                         "cannot run quickly runs it slowly and says so.")
    ap.add_argument("--crf", type=int, default=20,
                    help="quality, lower is better. 20 is a good default; on "
                         "h265 the same number is roughly one step better "
                         "quality at a smaller size.")
    ap.add_argument("--codec", choices=list(CODECS), default=DEFAULT_CODEC,
                    help="h265 (default) is ~40%% smaller at the same quality "
                         "and the better archival choice, but measured ~3x "
                         "slower to encode than h264 at the same preset. "
                         "YouTube accepts it; some upload paths on Instagram "
                         "and TikTok still prefer h264.")
    ap.add_argument("--preset", choices=list(PRESETS), default=DEFAULT_PRESET,
                    help="encoder speed/quality trade — THE lever on render "
                         "time. veryfast measured 1.6x faster than medium on "
                         "h265. medium is the default because a clip is "
                         "rendered once and watched many times; reach for a "
                         "faster preset while iterating on framing or captions.")
    ap.add_argument("--resolution", default="1080p",
                    help="source | 1080p | 1440p | 4k | WxH. 'source' keeps the "
                         "cropped region at native pixels with no scaling at "
                         "all — maximum fidelity, largest files. Platforms "
                         "deliver 1080p regardless.")
    ap.add_argument("--10bit", dest="ten_bit", action="store_true",
                    help="keep 10-bit precision (needs a 10-bit source such as "
                         "ProRes). Suppresses banding in skies and skin.")
    ap.add_argument("--transcript-source", choices=["auto", "captions", "whisper"],
                    default="auto",
                    help="where stage 2's words come from. auto uses the "
                         "caption track when the source is a URL and the track "
                         "has per-word timings, else transcribes. captions asks "
                         "for the track and still falls back to Whisper if it is "
                         "unusable. whisper never reads captions. NOTE a caption "
                         "track carries word STARTS and no ends, so cut points "
                         "close at the next word's onset rather than a measured "
                         "boundary.")
    ap.add_argument("--whisper", default="large-v3")
    ap.add_argument("--device", choices=list(DEVICES), default="auto",
                    help="auto tries the GPU and falls back to CPU; naming a "
                         "device explicitly is honoured and will not fall back")
    ap.add_argument("--language", default=None, help="e.g. ar, en. omit to autodetect")
    ap.add_argument("--vocab", default=None,
                    help="space-separated terms to bias transcription toward, "
                         "spelled the way you want them back. THE quality lever "
                         "on dialect and technical loanwords: measured 21 wrong "
                         "tokens to 7 on 12 minutes of Egyptian Arabic. Applies "
                         "to the whole file.")
    ap.add_argument("--vocab-file", type=Path, default=None,
                    help="read --vocab from a file, so a term list can be "
                         "versioned per channel or topic")
    ap.add_argument("--prompt", default=None,
                    help="free-text prompt seeding ONLY the first ~30s. Rarely "
                         "what you want — prefer --vocab, which holds for the "
                         "whole file. Measuring this on a short clip flatters it.")
    ap.add_argument("--prompt-file", type=Path, default=None,
                    help="read --prompt from a file")
    ap.add_argument("--denoise", action="store_true",
                    help="speech-band filter + FFT denoise before transcribing. "
                         "Measured 15 to 11 errors on noisy source (recorded in "
                         "a car), and ~20%% faster. Worth it on any field audio.")
    ap.add_argument("--fixups", type=Path, default=None,
                    help="file of 'wrong = right' word substitutions applied to "
                         "caption text after transcription. Timestamps are never "
                         "touched, so cuts are unaffected. Use for errors the "
                         "vocabulary will not take.")
    ap.add_argument("--font", default="Arial",
                    help="must be installed on the rendering host")
    ap.add_argument("--per-line", type=int, default=CAPTION_MAX_WORDS,
                    help="max words per caption line")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--plan-only", action="store_true",
                    help="transcribe + select, write plan.json, skip rendering")
    ap.add_argument("--plan", type=Path, default=None,
                    help="render this hand-edited plan.json instead of calling the model")
    return ap

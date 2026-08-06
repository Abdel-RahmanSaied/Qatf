"""The CLI run flow: preflight, drive the pipeline, print progress.

No pipeline logic. If you find yourself computing a timestamp here it belongs in
`qatf.pipeline`.
"""

from __future__ import annotations

import argparse
import json

from .. import pipeline
from ..core.config import get_settings
from ..core.errors import QatfError
from ..core.types import clips_from_dicts, clips_to_dicts
from ..core.utils import log, probe_video, ts_human
from ..llm import provider_from_settings


def preflight(args: argparse.Namespace) -> str | None:
    """Everything that should fail before a three-minute transcription starts.
    Returns a message, or None when good to go."""
    if not args.video.exists():
        return f"no such file: {args.video}"
    if args.min_len > args.max_len:
        return f"--min-len ({args.min_len}) is greater than --max-len ({args.max_len})"
    if args.plan and not args.plan.exists():
        return f"no such plan: {args.plan}"
    if args.prompt_file and not args.prompt_file.exists():
        return f"no such prompt file: {args.prompt_file}"
    if args.vocab_file and not args.vocab_file.exists():
        return f"no such vocab file: {args.vocab_file}"
    if args.fixups and not args.fixups.exists():
        return f"no such fixups file: {args.fixups}"
    if not args.plan:
        # stage 3 runs even under --plan-only, so the provider must be usable
        # either way. Ask the provider layer rather than checking one vendor's
        # env var — that check predated multi-provider support and was still
        # demanding ANTHROPIC_API_KEY while configured for OpenRouter.
        try:
            provider_from_settings()
        except QatfError as exc:
            return str(exc)
    try:
        pipeline.filtergraph(args.reframe, None)
        pipeline.encode.parse_resolution(args.resolution)
    except ValueError as exc:
        return str(exc)
    return None


def run(args: argparse.Namespace) -> int:
    if (problem := preflight(args)) is not None:
        log(problem)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    work = args.out / ".work"
    work.mkdir(exist_ok=True)

    log("[1/5] extracting audio" + (" (denoising)" if args.denoise else ""))
    wav = pipeline.extract_audio(args.video,
                                 pipeline.audio_path(work, args.denoise),
                                 denoise=args.denoise)

    # resolve before announcing, so the line says what will actually happen
    device = pipeline.resolve_device(args.device)
    if args.device == "auto":
        log(f"[2/5] transcribing with whisper {args.whisper} on {device} "
            f"(auto-selected)")
    else:
        log(f"[2/5] transcribing with whisper {args.whisper} on {device}")
    prompt = args.prompt
    if args.prompt_file:
        prompt = args.prompt_file.read_text(encoding="utf-8").strip()
    vocab = args.vocab
    if args.vocab_file:
        vocab = " ".join(args.vocab_file.read_text(encoding="utf-8").split())
    if vocab:
        log(f"      biasing vocabulary ({len(vocab.split())} terms, whole file)")
    if prompt:
        log(f"      seeding prompt ({len(prompt)} chars, first ~30s only)")
    transcript, cached = pipeline.transcribe_cached(
        wav, work, args.whisper, args.device, args.language, prompt, vocab
    )
    if cached:
        log(f"      reused cached transcript ({len(transcript)} words)")
    else:
        if transcript.device and transcript.device != device:
            log(f"      ran on {transcript.device} after a {device} load failure")
        if transcript.language:
            log(f"      detected language: {transcript.language} "
                f"(p={transcript.language_probability or 0:.2f})")
    if not transcript:
        log("no speech found — nothing to clip")
        return 1
    words = transcript.words

    if args.fixups:
        mapping = pipeline.fixups.load(args.fixups)
        words, n = pipeline.fixups.apply(words, mapping)
        log(f"      applied {n} word fixups from {len(mapping)} rules")

    # per-word corrections, if someone has written any. Picked up from the work
    # directory with no flag: the file is the interface, the same way the
    # transcript cache is. Fixups first, so a correction wins on the word it
    # names — see pipeline/edits.py.
    corrections = pipeline.edits.load(pipeline.edits.path(work))
    if corrections:
        words, applied, stale = pipeline.edits.apply(words, corrections)
        log(f"      applied {applied} word corrections" +
            (f", {len(stale)} stale (transcript moved — re-check them)"
             if stale else ""))

    plan_path = args.out / "plan.json"

    if args.plan:
        log(f"[3/5] reading plan from {args.plan} (no model call)")
        clips = clips_from_dicts(json.loads(args.plan.read_text(encoding="utf-8")))
        log("[4/5] re-snapping cuts to word boundaries")
        clips = [pipeline.snap(c, words) for c in clips]
    else:
        log(f"[3/5] asking {get_settings().model} for {args.clips} clips")
        clips = pipeline.plan_clips(words, args.clips, args.min_len, args.max_len)
        log("[4/5] snapped cuts to word boundaries")

    plan_path.write_text(json.dumps(clips_to_dicts(clips), indent=2, ensure_ascii=False),
                         encoding="utf-8")
    for c in clips:
        log(f"      {ts_human(c.start)}-{ts_human(c.end)} "
            f"({c.duration:4.1f}s, {c.score:.2f})  {c.title}")

    if args.plan_only:
        log(f"\nplan written to {plan_path} — review, edit, then rerun with "
            f"--plan {plan_path}")
        return 0
    if not clips:
        log("no clips survived the duration filter")
        return 1

    size = pipeline.encode.parse_resolution(args.resolution)
    if size is None:
        src = probe_video(args.video)
        size = pipeline.encode.native_size(src["width"], src["height"], args.reframe)
        log(f"      source {src['width']}x{src['height']} -> native "
            f"{size[0]}x{size[1]} (no scaling)")
    log(f"[5/5] rendering {len(clips)} clips at {size[0]}x{size[1]} "
        f"{args.codec} -preset {args.preset}"
        f"{' 10-bit' if args.ten_bit else ''}")
    pipeline.render_all(
        args.video, clips, words, args.out, work,
        mode=args.reframe, font=args.font, captions=not args.no_captions,
        per_line=args.per_line, crf=args.crf,
        width=size[0], height=size[1], codec=args.codec, ten_bit=args.ten_bit,
        preset=args.preset,
        on_clip=lambda i, total, clip, path: log(f"      -> {path}"),
    )

    log(f"\ndone. {len(clips)} clips in {args.out}")
    return 0

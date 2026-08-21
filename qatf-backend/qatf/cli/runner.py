"""The CLI run flow: preflight, drive the pipeline, print progress.

No pipeline logic. If you find yourself computing a timestamp here it belongs in
`qatf.pipeline`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import pipeline
from ..core.config import get_settings
from ..core.errors import QatfError
from ..core.types import clips_from_dicts, clips_to_dicts
from ..core.utils import log, probe_video, ts_human
from ..llm import provider_from_settings
from ..llm.presets import resolve_model


def preflight(args: argparse.Namespace) -> str | None:
    """Everything that should fail before a three-minute transcription starts.
    Returns a message, or None when good to go."""
    if pipeline.is_url(args.video):
        # Validated here as well as inside `fetch.download`, so a bad URL costs
        # nothing rather than being discovered after the output directory has
        # been made. The boundary itself lives in `fetch` — this is the second
        # line, not the line.
        try:
            pipeline.validate_url(args.video)
        except QatfError as exc:
            return str(exc)
        try:
            pipeline.fetch.ytdlp_module()
        except QatfError as exc:
            return str(exc)
    elif not Path(args.video).exists():
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
        # x0=0 only satisfies track mode's "needs a path or a seed"; this call
        # is here to validate the mode name, not to build the real graph.
        pipeline.filtergraph(args.reframe, None, x0=0)
        pipeline.encode.parse_resolution(args.resolution)
    except ValueError as exc:
        return str(exc)
    if args.reframe == "track":
        # Fail now rather than after transcribing an hour of audio. Stage 4b is
        # the first place this would otherwise be discovered, and by then the
        # expensive stage has already run.
        try:
            pipeline.detect.probe_detector(args.track_tier)
        except QatfError as exc:
            return str(exc)
    if not args.no_captions and (note := pipeline.font_warning(args.font)):
        # Logged here rather than returned, because this function's return value
        # means "abort". A missing font is the one preflight finding that must
        # not stop the run — see `captions.font_warning`. Emitted before stage 2
        # for the same reason as every other check in here: an hour of audio
        # should not transcribe before you learn the captions will be tofu.
        log(f"      WARNING {note}")
    return None


def run(args: argparse.Namespace) -> int:
    if (problem := preflight(args)) is not None:
        log(problem)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    work = args.out / ".work"
    work.mkdir(exist_ok=True)

    fetched = None
    video = Path(args.video)
    if pipeline.is_url(args.video):
        want_captions = args.transcript_source != "whisper"
        log("[0/5] fetching the video"
            + (" and its captions" if want_captions else ""))
        fetched = pipeline.fetch.download(
            args.video, work / "source",
            language=args.language, want_captions=want_captions)
        video = fetched.video
        log(f"      {fetched.title!r} ({fetched.duration}s) -> {video.name}")
        if want_captions and fetched.captions is None:
            log("      no usable caption track — transcription will run")

    log("[1/5] extracting audio" + (" (denoising)" if args.denoise else ""))
    wav = pipeline.extract_audio(video,
                                 pipeline.audio_path(work, args.denoise),
                                 denoise=args.denoise)

    transcript = None
    cached = False
    if args.transcript_source != "whisper" and fetched is not None and fetched.captions:
        raw = fetched.captions.read_text(encoding="utf-8")
        if pipeline.has_word_timings(raw):
            key = pipeline.asr.subs_cache_key(args.language)
            transcript = pipeline.read_cache(work, key)
            cached = transcript is not None
            if transcript is None:
                transcript = pipeline.to_transcript(raw, args.language)
                if transcript.words:
                    pipeline.write_cache(work, key, transcript)
            log(f"[2/5] read {len(transcript.words)} tokens from the caption "
                f"track — no transcription" + (" (cached)" if cached else ""))
            log("      word ENDS are bounds, not measurements: cuts close at "
                "the next word's onset")
        else:
            log("      the caption track is line-level (no per-word timings)")
    if transcript is None or not transcript.words:
        if args.transcript_source == "captions":
            log("      asked for captions and none were usable — transcribing")
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

    words, blanked = pipeline.health.repair(words)
    if blanked:
        log(f"      blanked {blanked} looped token(s) — the decoder repeated "
            f"itself; timings and word count are untouched")
    for note in pipeline.health.warnings(words):
        log(f"      NOTE {note}")

    # per-word corrections, if someone has written any. Picked up from the work
    # directory with no flag: word-edits.json is the interface — UNLIKE the
    # transcript cache's words-*.json, which is imported once and then
    # ignored, this file is re-imported whenever its mtime moves, so editing
    # it again after a first run still takes effect (see edits.load/save).
    # Fixups first, so a correction wins on the word it names.
    corrections = pipeline.edits.load(work, str(args.out.resolve()))
    if corrections:
        words, applied, stale = pipeline.edits.apply(words, corrections)
        log(f"      applied {applied} word corrections" +
            (f", {len(stale)} stale (transcript moved — re-check them)"
             if stale else ""))

    plan_path = args.out / "plan.json"
    # Derived from the transcript, not from a flag, so `--plan` re-snaps with
    # exactly the tail that produced the plan. A round trip that used a
    # different tail would move every boundary a little on each edit — the
    # drift `snap`'s idempotence exists to prevent, reintroduced sideways.
    tail = pipeline.tail_for(transcript.timing_source)

    if args.plan:
        log(f"[3/5] reading plan from {args.plan} (no model call)")
        clips = clips_from_dicts(json.loads(args.plan.read_text(encoding="utf-8")))
        log("[4/5] re-snapping cuts to word boundaries")
        clips = [pipeline.snap(c, words, tail=tail) for c in clips]
    else:
        settings = get_settings()
        log(f"[3/5] asking "
            f"{resolve_model(settings.llm_provider, settings.llm_model)} "
            f"for {args.clips} clips")
        clips = pipeline.plan_clips(
            words, args.clips, args.min_len, args.max_len,
            timing_source=transcript.timing_source)
        log("[4/5] snapped cuts to word boundaries")

    plan_path.write_text(json.dumps(clips_to_dicts(clips), indent=2, ensure_ascii=False),
                         encoding="utf-8")
    for c in clips:
        # The flag rides along with the listing rather than in a separate block,
        # because the operator is reading this line to decide about this clip.
        flag = pipeline.classify_duration(c, args.min_len, args.max_len)
        log(f"      {ts_human(c.start)}-{ts_human(c.end)} "
            f"({c.duration:4.1f}s, {c.score:.2f})  {c.title}"
            f"{'  [' + flag.upper() + ']' if flag else ''}")

    if args.plan_only:
        log(f"\nplan written to {plan_path} — review, edit, then rerun with "
            f"--plan {plan_path}")
        return 0
    if not clips:
        log("stage 3 returned no clips")
        return 1

    size = pipeline.encode.parse_resolution(args.resolution)
    src_dims = None
    if size is None or args.reframe == "track":
        src = probe_video(video)
        src_dims = (int(src["width"]), int(src["height"]))
    if size is None:
        size = pipeline.encode.native_size(*src_dims, args.reframe)
        log(f"      source {src_dims[0]}x{src_dims[1]} -> native "
            f"{size[0]}x{size[1]} (no scaling)")

    tracks = None
    if args.reframe == "track":
        log(f"[4b/5] tracking subjects across {len(clips)} clips "
            f"({args.track_tier})")
        tracks = pipeline.track_clips(video, clips, work, src_dims,
                                      tier=args.track_tier)
        lost = sum(1 for t in tracks if t.fallback)
        weak = sum(1 for t in tracks if not t.fallback and t.coverage < 0.5)
        log(f"      {len(tracks) - lost - weak} tracked, {weak} thin, "
            f"{lost} found no subject and fell back to a centre crop")
        if lost or weak:
            log(f"      review {work}/track-NN.json for those")

    log(f"[5/5] rendering {len(clips)} clips at {size[0]}x{size[1]} "
        f"{args.codec} -preset {args.preset}"
        f"{' 10-bit' if args.ten_bit else ''}")
    pipeline.render_all(
        video, clips, words, args.out, work,
        mode=args.reframe, font=args.font, captions=not args.no_captions,
        per_line=args.per_line, crf=args.crf,
        width=size[0], height=size[1], codec=args.codec, ten_bit=args.ten_bit,
        preset=args.preset, tracks=tracks, src=src_dims,
        on_clip=lambda i, total, clip, path: log(f"      -> {path}"),
    )

    log(f"\ndone. {len(clips)} clips in {args.out}")
    return 0

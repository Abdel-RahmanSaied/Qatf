"""What a job actually runs.

Module-level functions taking the store, rather than methods on it: this is the
only place that knows the stage order, and keeping it out of `store.py` means
persistence and orchestration can be read independently.

Cancellation is cooperative. `store.checkpoint()` is called between stages and
`should_stop` between clips; neither can interrupt an ffmpeg or Whisper call
already in flight.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .. import pipeline
from ..core.constants import DEFAULT_TRACK_TIER
from ..core.errors import EmptyPlan, NoSpeechFound
from ..core.types import Clip, Word, clips_from_dicts, clips_to_dicts
from ..core.utils import log, probe_video
from .model import JobState

if TYPE_CHECKING:
    from .store import JobStore


def baseline_words(transcript, opts: dict) -> tuple[list[Word], int]:
    """Transcript words with the job's fixups and repairs applied, and nothing
    else. Returns (words, blanked) — `blanked` is `health.repair`'s own count,
    kept rather than discarded so a caller can log it. It used to be thrown
    away here (`words, _ = pipeline.health.repair(words)`), which is why
    `cli/runner.py` could log "blanked N looped token(s)" and the identical
    job run over `POST /jobs` could not — see `run_pipeline`, the only caller
    that uses the second element.

    This is what a per-word correction is diffed against, so it must be what the
    caller was shown minus their own corrections — see `api.routers.plan`.
    Repair belongs here rather than in `caption_words` for that reason: put it
    downstream and the burned-in captions would disagree with what
    `GET /jobs/{id}/transcript` returns."""
    words = transcript.words
    mapping = opts.get("fixups") or {}
    if mapping:
        words, _ = pipeline.fixups.apply(words, mapping)
    words, blanked = pipeline.health.repair(words)
    return words, blanked


def caption_words(transcript, opts: dict, work: Path | None = None,
                  job_id: str | None = None) -> tuple[list[Word], int, int, int]:
    """The text that actually gets burned in: fixups, then per-word corrections.

    Returns (words, blanked, corrections applied, corrections gone stale).

    Both layers are applied here rather than at transcription time, so either can
    change between renders without re-transcribing — and so `run_render` gets
    exactly the same text `run_pipeline` did.

    Order matters: fixups are a global rule, corrections are a specific override,
    so a correction wins on the word it names.

    `job_id` is the overlay's scope (see `pipeline.edits.load`) and is required
    whenever `work` is — both name the same job, just at different layers.
    Enforced, not just documented: `edits.load(work, None)` would not raise —
    SQLite would happily bind NULL and match zero rows, which reads as "no
    corrections for this job" instead of the caller bug it actually is, and
    that would stay silent until someone noticed a job's corrections never
    took effect."""
    words, blanked = baseline_words(transcript, opts)
    if work is None:
        return words, blanked, 0, 0
    if not job_id:
        raise ValueError("caption_words: job_id is required whenever work is given")
    words, applied, stale = pipeline.edits.apply(
        words, pipeline.edits.load(work, job_id))
    return words, blanked, applied, len(stale)


def fetch_source(store: JobStore, job_id: str, job) -> object | None:
    """Stage 0. Returns what was fetched, or None for a job that needs no fetch.

    Only `source="youtube"` jobs fetch. The job's `video` is rewritten to the
    downloaded path here, so every later stage is handed a local file and none
    of them learns that a URL was ever involved — the same shape as an upload,
    which also rewrites `video` before submitting."""
    if job.source != "youtube" or not job.url:
        return None

    want_captions = job.options.get("transcript_source", "auto") != "whisper"
    store.update(job_id, state=JobState.fetching.value,
                 message="[0/5] fetching the video"
                         + (" and its captions" if want_captions else ""))
    fetched = pipeline.fetch.download(
        job.url, job.source_dir(store.root),
        language=job.options.get("language"),
        want_captions=want_captions,
    )
    store.update(job_id, video=str(fetched.video))
    log(f"stage 0: fetched {fetched.title!r} ({fetched.duration}s)")
    return fetched


def obtain_transcript(store: JobStore, job_id: str, opts: dict, work: Path,
                      wav: Path, fetched) -> tuple[object, bool, str]:
    """Stage 2, from a caption track when one is usable and Whisper otherwise.

    Returns (transcript, was_cached, cache key).

    The preference is a request, not a guarantee, and the fallback is LOUD on
    purpose. Falling back here is a fall *up* in quality — Whisper measures both
    edges of every word, where a caption track bounds one — so the only thing
    lost is the minutes stage 2 costs, which is worth a log line rather than a
    failed job. That is the opposite of `--device cuda` and `--reframe track`,
    which refuse rather than degrade, and the difference is exactly that those
    two have no better alternative to fall back TO."""
    preference = opts.get("transcript_source", "auto")
    language = opts.get("language")

    if preference != "whisper" and fetched is not None and fetched.captions:
        raw = Path(fetched.captions).read_text(encoding="utf-8")
        if pipeline.subs.has_word_timings(raw):
            key = pipeline.asr.subs_cache_key(language)
            cached = pipeline.read_cache(work, key)
            if cached is not None:
                return cached, True, key
            store.update(job_id, state=JobState.transcribing.value,
                         message="[2/5] reading the caption track (no transcription)")
            transcript = pipeline.subs.to_transcript(raw, language)
            if transcript.words:
                pipeline.write_cache(work, key, transcript)
                log(f"stage 2: {len(transcript.words)} tokens from captions — "
                    f"word ends are BOUNDS, not measurements (see timing_source)")
                return transcript, False, key
            note = "the caption track parsed to no words"
        else:
            note = "the caption track has no per-word timings (line-level only)"
    elif preference == "captions":
        note = "no caption track was available"
    else:
        note = ""

    if preference == "captions" and note:
        log(f"stage 2: asked for captions but {note} — transcribing instead")
        store.update(job_id, message=f"[2/5] captions unusable ({note}) — transcribing")

    requested = opts.get("device", "auto")
    device = pipeline.resolve_device(requested)
    store.update(job_id, state=JobState.transcribing.value,
                 message=f"[2/5] transcribing with whisper {opts['whisper']} on "
                         f"{device}{' (auto-selected)' if requested == 'auto' else ''}")
    key = pipeline.cache_key(opts["whisper"], language,
                             opts.get("initial_prompt"), opts.get("hotwords"))
    transcript, cached = pipeline.transcribe_cached(
        wav, work, opts["whisper"], requested, language,
        opts.get("initial_prompt"), opts.get("hotwords")
    )
    return transcript, cached, key


def run_pipeline(store: JobStore, job_id: str) -> None:
    """All five stages. Stops at `planned` when auto_render is off."""
    job = store.require(job_id)
    opts = job.options
    work = job.work_dir(store.root)

    store.checkpoint(job_id)
    fetched = fetch_source(store, job_id, job)
    if fetched is not None:
        job = store.require(job_id)      # stage 0 rewrote `video`

    store.checkpoint(job_id)
    denoise = opts.get("denoise", False)
    store.update(job_id, state=JobState.extracting.value,
                 message="[1/5] extracting audio" + (" (denoising)" if denoise else ""))
    wav = pipeline.extract_audio(Path(job.video),
                                 pipeline.audio_path(work, denoise),
                                 denoise=denoise)

    store.checkpoint(job_id)
    transcript, cached, transcript_key = obtain_transcript(
        store, job_id, opts, work, wav, fetched)
    store.update(job_id, transcript_key=transcript_key)
    if not transcript:
        raise NoSpeechFound("no speech found — nothing to clip")
    words, blanked, _, _ = caption_words(transcript, opts, work, job_id)
    # `cli/runner.py` logs both of these after the same call; `warnings()` had
    # no other caller, so a job run over `POST /jobs` logged neither and a
    # repeated-token repair or a bad timing was invisible on the server side —
    # visible only to someone who happened to run the same video through the
    # CLI too. `warnings()` here sees `words` after edits are applied rather
    # than right after repair, unlike the CLI's ordering; that's fine because
    # fixups and edits only ever touch `Word.text`, never a timing (the core
    # invariant), so the timing-defect list `warnings()` reports is identical
    # either side of them.
    if blanked:
        log(f"      blanked {blanked} looped token(s) — the decoder repeated "
            f"itself; timings and word count are untouched")
    for note in pipeline.health.warnings(words):
        log(f"      NOTE {note}")
    # `transcript.device` alone, with no local fallback: a caption transcript
    # ran on no device at all and must report None rather than inheriting
    # whatever `resolve_device` would have answered. The Whisper path already
    # records the device it actually used on the Transcript — including after
    # the CUDA-to-CPU fallback, which is the whole reason that field exists.
    store.update(job_id, language=transcript.language,
                 word_count=len(words), transcript_cached=cached,
                 device=transcript.device)

    store.checkpoint(job_id)
    # the store's settings, not the process-wide ones — see JobStore.__init__
    settings = store.settings
    model = settings.model
    store.update(job_id, state=JobState.selecting.value,
                 message=f"[3/5] asking {settings.llm_provider}:{model} "
                         f"for {opts['clips']} clips")
    # stage 4 (snap) runs inside plan_clips — deterministic, no model.
    # `settings` is passed, not looked up: stage 3 is the only part of the
    # pipeline that opens a network connection and spends a credential, so it
    # must use the object this app was built with, not the environment's.
    clips = pipeline.plan_clips(words, opts["clips"],
                                opts["min_len"], opts["max_len"],
                                model=model, settings=settings,
                                timing_source=transcript.timing_source)
    store.update(job_id, clips=clips_to_dicts(clips),
                 message="[4/5] snapped cuts to word boundaries")

    if not clips:
        store.update(job_id, state=JobState.planned.value,
                     message="no clips survived the duration filter")
        return

    if not opts.get("auto_render", True):
        store.update(job_id, state=JobState.planned.value,
                     message="plan ready — PUT /plan to edit, POST /render to encode")
        return

    store.checkpoint(job_id)
    render_plan(store, job_id, words, clips)


def run_render(store: JobStore, job_id: str) -> None:
    """Stage 5 alone, over whatever plan the job currently holds. No model call —
    this is what makes the hand-edit round trip cheap."""
    job = store.require(job_id)
    clips = clips_from_dicts(job.clips)
    if not clips:
        raise EmptyPlan("job has no plan to render")
    transcript = store.transcript_for(job)
    if transcript is None:
        raise EmptyPlan("job has no transcript to caption from")
    words, _, _, _ = caption_words(transcript, job.options,
                                   job.work_dir(store.root), job_id)
    render_plan(store, job_id, words, clips)


def render_plan(store: JobStore, job_id: str,
                words: list[Word], clips: list[Clip]) -> None:
    job = store.require(job_id)
    opts = job.options
    out_dir = job.out_dir(store.root)
    for stale in out_dir.glob("*.mp4"):
        stale.unlink()

    # The CLI warns about this in `preflight`; the server has no preflight, and
    # under Docker the RENDERING host is the server rather than the caller's
    # machine — so this path is the one that actually matters. Warning only in
    # `cli/runner.py` would leave the deployment that renders in production the
    # one deployment that says nothing, which is the same front-end asymmetry
    # `baseline_words` had to fix for the repetition-repair count.
    if opts.get("captions", True) and (note := pipeline.font_warning(opts["font"])):
        log(f"      WARNING {note}")

    store.update(job_id, state=JobState.rendering.value, outputs=[],
                 output_sizes={},
                 message=f"[5/5] rendering {len(clips)} clips")

    done: list[str] = []
    sizes: dict[str, int] = {}

    def on_clip(index: int, total: int, clip: Clip, path: Path) -> None:
        done.append(path.name)
        # Record the size here, where the file was just written and is already
        # in the OS cache. Deriving it on read cost one stat() per clip per job
        # per request and dominated `GET /jobs`; a rendered clip never changes
        # size, so this belongs to the write.
        try:
            sizes[path.name] = path.stat().st_size
        except OSError:
            pass
        store.update(job_id, outputs=list(done), output_sizes=dict(sizes),
                     message=f"[5/5] rendered {index}/{total}: {path.name}")

    mode = opts["reframe"]
    size = pipeline.encode.parse_resolution(opts.get("resolution", "1080p"))
    # `track` needs the real dimensions whatever the requested resolution is —
    # stage 4c solves a crop path in normalised units and stage 5b turns it back
    # into pixels against the SOURCE frame, not the output one.
    src_dims = None
    if size is None or mode == "track":
        src = probe_video(job.video)
        src_dims = (int(src["width"]), int(src["height"]))
    if size is None:
        size = pipeline.encode.native_size(*src_dims, mode)

    tracks = None
    if mode == "track":
        # Stages 4b and 4c. Raises DetectorNotAvailable (503) if OpenCV or the
        # weights are missing, rather than degrading to a centre crop — asking
        # for tracking and quietly getting a static crop is the same lie as
        # asking for `device: cuda` and quietly getting CPU.
        store.update(job_id, message=f"[5/5] tracking subjects across "
                                     f"{len(clips)} clips")
        tracks = pipeline.track_clips(
            Path(job.video), clips, job.work_dir(store.root), src_dims,
            tier=opts.get("track_tier", DEFAULT_TRACK_TIER))
        lost = sum(1 for t in tracks if t.fallback)
        if lost:
            store.update(job_id, message=f"[5/5] {lost}/{len(tracks)} clips found "
                                         f"no subject and fall back to a centre crop")

    pipeline.render_all(
        Path(job.video), clips, words, out_dir, job.work_dir(store.root),
        mode=mode, font=opts["font"], captions=opts.get("captions", True),
        per_line=opts.get("per_line", 4), crf=opts.get("crf", 20),
        width=size[0], height=size[1],
        codec=opts.get("codec", pipeline.encode.DEFAULT_CODEC),
        ten_bit=opts.get("ten_bit", False),
        preset=opts.get("preset", pipeline.encode.DEFAULT_PRESET),
        tracks=tracks, src=src_dims,
        on_clip=on_clip,
        should_stop=lambda: store.cancel_requested(job_id),
    )

    store.checkpoint(job_id)
    store.update(job_id, state=JobState.done.value, message=f"done. {len(done)} clips")

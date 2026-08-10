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
from ..core.utils import probe_video
from .model import JobState

if TYPE_CHECKING:
    from .store import JobStore


def baseline_words(transcript, opts: dict) -> list[Word]:
    """Transcript words with the job's fixups and repairs applied, and nothing
    else.

    This is what a per-word correction is diffed against, so it must be what the
    caller was shown minus their own corrections — see `api.routers.plan`.
    Repair belongs here rather than in `caption_words` for that reason: put it
    downstream and the burned-in captions would disagree with what
    `GET /jobs/{id}/transcript` returns."""
    words = transcript.words
    mapping = opts.get("fixups") or {}
    if mapping:
        words, _ = pipeline.fixups.apply(words, mapping)
    words, _ = pipeline.health.repair(words)
    return words


def caption_words(transcript, opts: dict,
                  work: Path | None = None) -> tuple[list[Word], int, int]:
    """The text that actually gets burned in: fixups, then per-word corrections.

    Returns (words, corrections applied, corrections gone stale).

    Both layers are applied here rather than at transcription time, so either can
    change between renders without re-transcribing — and so `run_render` gets
    exactly the same text `run_pipeline` did.

    Order matters: fixups are a global rule, corrections are a specific override,
    so a correction wins on the word it names."""
    words = baseline_words(transcript, opts)
    if work is None:
        return words, 0, 0
    words, applied, stale = pipeline.edits.apply(
        words, pipeline.edits.load(pipeline.edits.path(work)))
    return words, applied, len(stale)


def run_pipeline(store: JobStore, job_id: str) -> None:
    """All five stages. Stops at `planned` when auto_render is off."""
    job = store.require(job_id)
    opts = job.options
    work = job.work_dir(store.root)

    store.checkpoint(job_id)
    denoise = opts.get("denoise", False)
    store.update(job_id, state=JobState.extracting.value,
                 message="[1/5] extracting audio" + (" (denoising)" if denoise else ""))
    wav = pipeline.extract_audio(Path(job.video),
                                 pipeline.audio_path(work, denoise),
                                 denoise=denoise)

    store.checkpoint(job_id)
    requested = opts.get("device", "auto")
    device = pipeline.resolve_device(requested)
    store.update(job_id, state=JobState.transcribing.value,
                 message=f"[2/5] transcribing with whisper {opts['whisper']} on "
                         f"{device}{' (auto-selected)' if requested == 'auto' else ''}")
    transcript, cached = pipeline.transcribe_cached(
        wav, work, opts["whisper"], requested, opts.get("language"),
        opts.get("initial_prompt"), opts.get("hotwords")
    )
    if not transcript:
        raise NoSpeechFound("no speech found — nothing to clip")
    words, _, _ = caption_words(transcript, opts, work)
    store.update(job_id, language=transcript.language,
                 word_count=len(words), transcript_cached=cached,
                 device=transcript.device or device)

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
                                model=model, settings=settings)
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
    words, _, _ = caption_words(transcript, job.options, job.work_dir(store.root))
    render_plan(store, job_id, words, clips)


def render_plan(store: JobStore, job_id: str,
                words: list[Word], clips: list[Clip]) -> None:
    job = store.require(job_id)
    opts = job.options
    out_dir = job.out_dir(store.root)
    for stale in out_dir.glob("*.mp4"):
        stale.unlink()

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

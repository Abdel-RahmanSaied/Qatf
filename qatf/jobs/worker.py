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
from ..core.errors import EmptyPlan, NoSpeechFound
from ..core.types import Clip, Word, clips_from_dicts, clips_to_dicts
from ..core.utils import probe_video
from .model import JobState

if TYPE_CHECKING:
    from .store import JobStore


def caption_words(transcript, opts: dict) -> list[Word]:
    """Transcript words with the job's fixups applied.

    Applied here rather than at transcription time so the map can change between
    renders without re-transcribing — and so `run_render` gets the same text as
    `run_pipeline` did."""
    words = transcript.words
    mapping = opts.get("fixups") or {}
    if mapping:
        words, _ = pipeline.fixups.apply(words, mapping)
    return words


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
    words = caption_words(transcript, opts)
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
    # stage 4 (snap) runs inside plan_clips — deterministic, no model
    clips = pipeline.plan_clips(words, opts["clips"],
                                opts["min_len"], opts["max_len"], model=model)
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
    render_plan(store, job_id, caption_words(transcript, job.options), clips)


def render_plan(store: JobStore, job_id: str,
                words: list[Word], clips: list[Clip]) -> None:
    job = store.require(job_id)
    opts = job.options
    out_dir = job.out_dir(store.root)
    for stale in out_dir.glob("*.mp4"):
        stale.unlink()

    store.update(job_id, state=JobState.rendering.value, outputs=[],
                 message=f"[5/5] rendering {len(clips)} clips")

    done: list[str] = []

    def on_clip(index: int, total: int, clip: Clip, path: Path) -> None:
        done.append(path.name)
        store.update(job_id, outputs=list(done),
                     message=f"[5/5] rendered {index}/{total}: {path.name}")

    size = pipeline.encode.parse_resolution(opts.get("resolution", "1080p"))
    if size is None:
        src = probe_video(job.video)
        size = pipeline.encode.native_size(src["width"], src["height"],
                                           opts["reframe"])

    pipeline.render_all(
        Path(job.video), clips, words, out_dir, job.work_dir(store.root),
        mode=opts["reframe"], font=opts["font"], captions=opts.get("captions", True),
        per_line=opts.get("per_line", 4), crf=opts.get("crf", 20),
        width=size[0], height=size[1],
        codec=opts.get("codec", "h264"), ten_bit=opts.get("ten_bit", False),
        on_clip=on_clip,
        should_stop=lambda: store.cancel_requested(job_id),
    )

    store.checkpoint(job_id)
    store.update(job_id, state=JobState.done.value, message=f"done. {len(done)} clips")

"""Stage 2 — transcription with word timestamps.

This is where every real timing in the system comes from. Stage 4 snaps onto
these and nothing else.

Device selection is two-layered on purpose. `resolve_device` asks CTranslate2
whether a usable CUDA device exists, which is what decides the log message and
`/healthz`. `load_model` then wraps the actual model construction, because
availability and usability are different things: a driver mismatch, an unsupported
compute capability, or an out-of-memory GPU all report a device and then fail on
load. Only the second layer can catch those.

UNVERIFIED: `model.transcribe`'s exact `info` attributes and `WhisperModel`
kwargs are from documentation, not from a run.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path

from ..core.errors import SeedTooLong, TranscriberNotAvailable
from ..core.types import Transcript, Word, words_from_dicts, words_to_dicts
from ..core.utils import log, slugify

DEVICES = ("auto", "cuda", "cpu")

#: Model names faster-whisper resolves to a published checkpoint.
#:
#: This is an allowlist, and it matters at a trust boundary. `WhisperModel`
#: accepts a "size OR path": anything not in this set is treated as a HuggingFace
#: repo id or a local directory, so an unvalidated name lets a caller make the
#: server fetch an arbitrary repo and hand its weights to CTranslate2 — remote
#: fetch plus untrusted native deserialisation, chosen over HTTP.
#:
#: The CLI is deliberately NOT restricted to this list. A local user pointing at
#: their own converted model is legitimate and crosses no boundary; a `POST /jobs`
#: body does. See `qatf.api.schemas.JobOptions`.
MODEL_SIZES = frozenset({
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large", "large-v1", "large-v2", "large-v3",
    "large-v3-turbo", "turbo", "distil-small.en", "distil-medium.en",
    "distil-large-v2", "distil-large-v3",
})

#: int8 on CPU, float16 on GPU. CTranslate2 falls back internally if a GPU
#: cannot do float16, so this does not need per-architecture special casing.
_COMPUTE = {"cuda": "float16", "cpu": "int8"}

#: models heavy enough that CPU inference is painful rather than merely slower
_HEAVY = ("large", "medium")

#: Whisper's decoder context is 448 tokens and the prompt/hotwords share a
#: fraction of it. Overrun it and faster-whisper dies with the deeply unhelpful
#: "ValueError: The maximum decoding length must be > 0" — nothing names the
#: vocabulary as the cause. Conservative character budget; Arabic and other
#: non-Latin scripts run 2-3 chars per token, so this errs small on purpose.
HOTWORD_CHAR_BUDGET = 300
PROMPT_CHAR_BUDGET = 800


@functools.lru_cache(maxsize=1)
def cuda_device_count() -> int:
    """Usable CUDA devices according to CTranslate2 — the engine faster-whisper
    actually runs on. `nvidia-smi` seeing a card is not the same question.

    Cached: the answer cannot change within a process, and `/healthz` asks it
    twice per request (once directly, once inside `resolve_device`) on an
    endpoint that gets polled hard."""
    try:
        import ctranslate2
    except ImportError:
        return 0
    try:
        return int(ctranslate2.get_cuda_device_count())
    except Exception:                       # noqa: BLE001 — any failure means unusable
        return 0


def compute_type_for(device: str) -> str:
    return _COMPUTE.get(device, "int8")


def resolve_device(requested: str = "auto") -> str:
    """`auto` prefers CUDA and falls back to CPU. An explicit device is honoured
    as written — asking for `cuda` and silently getting `cpu` would turn a
    20-minute benchmark into a lie."""
    if requested not in DEVICES:
        raise ValueError(f"unknown device {requested!r}, expected one of {DEVICES}")
    if requested != "auto":
        return requested
    return "cuda" if cuda_device_count() > 0 else "cpu"


def whisper_model_class():
    """Import WhisperModel, or say which package is missing and how to get it.

    Imported inside the functions that need it — CLAUDE.md's import-cost rule —
    which is exactly why the failure needs typing here: a lazy import turns a
    missing extra into a `ModuleNotFoundError` raised from the middle of a
    running job rather than at startup. It reached the job record raw, with no
    status code and no install hint, until a live API run surfaced it."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriberNotAvailable(
            "stage 2 needs faster-whisper — install it with "
            "`pip install -e \".[all]\"`, or pass --plan to render a plan you "
            "already have and skip transcription entirely"
        ) from exc
    return WhisperModel


def load_model(model_size: str, device: str, requested: str = "auto"):
    """Construct a WhisperModel, falling back to CPU when a GPU load fails.

    The fallback applies only when the device was auto-selected. If the caller
    named `cuda` explicitly, the failure is raised — they asked a specific
    question and deserve the real answer."""
    WhisperModel = whisper_model_class()

    try:
        return WhisperModel(model_size, device=device,
                            compute_type=compute_type_for(device)), device
    except Exception as exc:                # noqa: BLE001 — CUDA failures are not one type
        if device != "cuda" or requested == "cuda":
            raise
        log(f"      GPU reported available but would not load ({type(exc).__name__}: "
            f"{exc}); falling back to CPU")
        return WhisperModel(model_size, device="cpu",
                            compute_type=compute_type_for("cpu")), "cpu"


#: handles from os.add_dll_directory must stay alive — the directory is removed
#: from the search path when the handle is garbage-collected
_DLL_HANDLES: list = []


def register_cuda_dlls() -> int:
    """Windows: make pip-installed CUDA runtime libraries findable.

    `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` puts the DLLs under
    site-packages/nvidia/*/bin, which is NOT on the default DLL search path.
    PyTorch registers those directories on import; CTranslate2 does not — so
    without this the libraries are installed and still fail to load with
    'cublas64_12.dll is not found'.

    BOTH mechanisms are needed, and this is the part that is easy to get wrong:
    `os.add_dll_directory` only affects loads that go through Python's own
    LoadLibraryEx call, which covers extension modules. CTranslate2 resolves
    cuBLAS from inside its C++ layer with a plain LoadLibrary, and that searches
    PATH. Registering the directory without also prepending PATH looks correct
    and changes nothing. Returns how many directories were added."""
    if os.name != "nt" or _DLL_HANDLES:
        return len(_DLL_HANDLES)
    import site

    found: list[str] = []
    roots = [*site.getsitepackages(), site.getusersitepackages()]
    for root in roots:
        nvidia = Path(root) / "nvidia"
        if not nvidia.is_dir():
            continue
        for lib_dir in sorted(nvidia.glob("*/bin")):
            if any(lib_dir.glob("*.dll")):
                found.append(str(lib_dir))
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(str(lib_dir)))
                except OSError:             # directory vanished mid-scan
                    pass
    if found:
        os.environ["PATH"] = os.pathsep.join([*found, os.environ.get("PATH", "")])
    return len(found)


#: Decode parameters, in one place so a sweep changes one thing at a time.
#:
#: EVERY VALUE HERE IS THE CURRENT BEHAVIOUR, NOT A MEASURED OPTIMUM. The only
#: non-default entry is min_silence_duration_ms, which this project set to 500
#: for speed; faster-whisper's default under vad_filter is 160, and a higher
#: value merges across short pauses into longer chunks (capped at
#: max_speech_duration_s = 30s). A 30s window of noisy audio that decodes to
#: almost nothing is how 15 seconds of speech became one `آآ` token. That is
#: hypothesis #1 in docs/quality.md, not a conclusion.
DECODE: dict = {
    "vad_filter": True,
    "vad_parameters": {"min_silence_duration_ms": 500},
}


def merge_decode(overrides: dict | None) -> dict:
    """DECODE with `overrides` merged one level deep, leaving DECODE untouched.

    One level is enough — `vad_parameters` is the only nested key — and a deeper
    merge would hide which knob a sweep actually moved. A shallow `dict(DECODE)`
    is not enough on its own: it copies the top level but leaves `vad_parameters`
    shared by reference, so `merged["vad_parameters"]["x"] = y` would mutate the
    module-level dict every later run inherits. Each nested dict is copied too,
    and a nested override updates the copy rather than replacing it, so
    overriding `speech_pad_ms` alone still keeps `min_silence_duration_ms`."""
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DECODE.items()}
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _transcribe_on(wav: Path, model_size: str, device: str,
                   language: str | None,
                   initial_prompt: str | None = None,
                   hotwords: str | None = None,
                   decode: dict | None = None) -> Transcript:
    """Build the model AND fully consume the segment generator.

    Consuming inside this function is the whole point. `WhisperModel(...)`
    constructs happily on a GPU whose CUDA libraries are missing — faster-whisper
    initialises lazily, so the real failure (`cublas64_12.dll is not found`)
    surfaces on the first encode, which happens while iterating `segments`. A
    guard around construction alone never sees it."""
    if device == "cuda":
        register_cuda_dlls()
    WhisperModel = whisper_model_class()

    model = WhisperModel(model_size, device=device,
                         compute_type=compute_type_for(device))
    segments, info = model.transcribe(
        str(wav),
        language=language,          # None => autodetect; "ar" / "en" to force
        word_timestamps=True,
        # VAD and any other decode knob live in DECODE (above), so a sweep can
        # change exactly one of them per run instead of editing this call.
        **merge_decode(decode),
        # `hotwords` biases vocabulary on EVERY window; `initial_prompt` seeds
        # only the first. On a 12-minute Egyptian Arabic file, measured over the
        # whole transcript:
        #     baseline         21 wrong,  8 right, 13 wrong after 300s
        #     initial_prompt   16 wrong, 12 right, 16 wrong after 300s
        #     hotwords          7 wrong, 22 right,  6 wrong after 300s
        # initial_prompt looks fine on a short clip and then decays — measuring
        # it on a slice that starts where the prompt was applied flatters it
        # badly. Combining both scored no better and dropped 118 words.
        initial_prompt=initial_prompt or None,
        hotwords=hotwords or None,
    )

    words: list[Word] = []
    for seg in segments:  # generator — this is where the work actually happens
        for w in (seg.words or []):
            token = w.word.strip()
            if token:
                words.append(Word(token, float(w.start), float(w.end)))

    return Transcript(
        words=words,
        language=getattr(info, "language", None),
        language_probability=getattr(info, "language_probability", None),
        device=device,
        compute_type=compute_type_for(device),
    )


def check_seed_budget(initial_prompt: str | None, hotwords: str | None) -> None:
    """Reject an over-long vocabulary before Whisper does, with a usable message.

    Whisper spends part of its 448-token context on the prompt and hotwords. Go
    over and decoding has no room left, which surfaces as `ValueError: The
    maximum decoding length must be > 0` from deep inside faster-whisper —
    a message that names neither the vocabulary nor the limit."""
    if hotwords and len(hotwords) > HOTWORD_CHAR_BUDGET:
        raise SeedTooLong(
            f"--vocab is {len(hotwords)} characters ({len(hotwords.split())} "
            f"terms); the budget is ~{HOTWORD_CHAR_BUDGET}. Whisper shares a "
            f"448-token context between the vocabulary and decoding, so an "
            f"over-long list leaves no room to transcribe. Keep the terms the "
            f"model actually gets wrong and drop the rest."
        )
    if initial_prompt and len(initial_prompt) > PROMPT_CHAR_BUDGET:
        raise SeedTooLong(
            f"--prompt is {len(initial_prompt)} characters; the budget is "
            f"~{PROMPT_CHAR_BUDGET}."
        )


def transcribe(wav: Path, model_size: str, device: str,
               language: str | None,
               initial_prompt: str | None = None,
               hotwords: str | None = None,
               decode: dict | None = None) -> Transcript:
    """`device` may be auto/cuda/cpu. The device actually used is recorded on the
    returned Transcript, so callers report what happened rather than what was
    asked for.

    A CUDA failure falls back to CPU only when the device was auto-selected; an
    explicit `--device cuda` raises, because the caller asked a specific
    question. The fallback wraps the whole transcription, not just the model
    load — see `_transcribe_on`."""
    check_seed_budget(initial_prompt, hotwords)
    resolved = resolve_device(device)
    if resolved == "cpu" and model_size.startswith(_HEAVY):
        log(f"      running {model_size} on CPU — this is slow; "
            f"--whisper small (or base) is far quicker if quality allows")

    try:
        return _transcribe_on(wav, model_size, resolved, language,
                              initial_prompt, hotwords, decode)
    except Exception as exc:                # noqa: BLE001 — CUDA failures are not one type
        if resolved != "cuda" or device == "cuda":
            raise
        log(f"      GPU transcription failed ({type(exc).__name__}: {exc})")
        log("      falling back to CPU. For GPU speed install the CUDA runtime: "
            "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
        return _transcribe_on(wav, model_size, "cpu", language,
                              initial_prompt, hotwords, decode)


def cache_path(work: Path, model_size: str, language: str | None,
               initial_prompt: str | None = None,
               hotwords: str | None = None) -> Path:
    """The cache key includes the model, the forced language, AND the prompt.

    Keying on the output directory alone silently reused an English transcript
    after `--language ar` — the one axis most likely to be wrong. The prompt
    belongs in the key for the same reason: it changes the transcript, so a
    cache hit across two different prompts would quietly serve the old wording
    and make the prompt look like it did nothing."""
    # BOTH components are slugified, and the language one is a security fix, not
    # tidiness: `language` is a free-form caller-supplied string, and
    # `work / f"words-large-v3-{language}.json"` with language="../../../x"
    # resolves outside the work directory. write_cache then mkdirs the parent and
    # writes there — an arbitrary file write chosen by a POST body. slugify
    # leaves a real language code untouched ("ar" -> "ar"), so no existing cache
    # is invalidated.
    stem = f"words-{slugify(model_size)}-{slugify(language) if language else 'auto'}"
    seed = f"{initial_prompt or ''}\x00{hotwords or ''}"
    if seed.strip("\x00"):
        stem += "-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return work / f"{stem}.json"


def read_cache(path: Path) -> Transcript:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):          # pre-0.2 format: a bare word list
        raw = {"words": raw}
    return Transcript(
        words=words_from_dicts(raw.get("words", [])),
        language=raw.get("language"),
        language_probability=raw.get("language_probability"),
        device=raw.get("device"),
        compute_type=raw.get("compute_type"),
    )


def write_cache(path: Path, transcript: Transcript) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "language": transcript.language,
                "language_probability": transcript.language_probability,
                # recorded, but deliberately NOT part of the cache key: a CPU and
                # a GPU transcript of the same audio are interchangeable enough
                # that re-transcribing on fallback would be pure waste
                "device": transcript.device,
                "compute_type": transcript.compute_type,
                "words": words_to_dicts(transcript.words),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def transcribe_cached(wav: Path, work: Path, model_size: str, device: str,
                      language: str | None,
                      initial_prompt: str | None = None,
                      hotwords: str | None = None,
                      decode: dict | None = None) -> tuple[Transcript, bool]:
    """Returns (transcript, was_cached). Delete the cache file to re-transcribe.

    `decode` is deliberately NOT part of `cache_path`'s key. During a decode-
    parameter sweep each run is written to its own explicitly named output file
    and compared side by side, so the cache key must stay stable across the
    sweep or every run after the first would silently reuse the first run's
    transcript. Whether a decode override should invalidate the cache once the
    sweep is over and a value gets promoted into DECODE is an open question —
    left for whoever does that promotion, not decided here."""
    cache = cache_path(work, model_size, language, initial_prompt, hotwords)
    if cache.exists():
        return read_cache(cache), True

    transcript = transcribe(wav, model_size, device, language,
                            initial_prompt, hotwords, decode)
    write_cache(cache, transcript)
    return transcript, False

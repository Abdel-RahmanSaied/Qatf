"""Stage 0 — resolve a caller-supplied URL to a local file.

Runs BEFORE stage 1. Everything downstream is handed a path on disk and never
learns that a URL was involved, which is the point: a new *source* must not
become a new *pipeline*. Stages 1-5 are untouched by this file.

This module owns a trust boundary, and it is a new kind for this project.
`media_root` bounds which FILES a request may name; nothing bounded which URLS
one may name, because until now none could. yt-dlp accepts `file://`, plain
local paths, and carries extractors for a thousand sites — so an unvalidated
string from a `POST` body is simultaneously a local-file read and an
outbound-request primitive, aimed by whoever can reach the API.

`validate_url` is therefore enforced HERE rather than only in the pydantic
schema, for the same reason `asr.cache_path` slugifies `language` itself: the
risk belongs to the thing doing the work, and the CLI has to be held to the same
rule as an HTTP caller. The schema check is a second line, not the line.

UNVERIFIED: yt-dlp's behaviour against real YouTube is exercised by
`tests/verify_youtube.py`, which needs the network and skips without it. The
parsing half is pinned offline in `smoke_pipeline.py` from a fixture.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..core.constants import LANGUAGE_TAG_PATTERN, YOUTUBE_HOSTS
from ..core.errors import (
    CaptionLanguageInvalid,
    FetcherNotAvailable,
    SourceNotFetchable,
    UnsupportedSourceUrl,
)
from ..core.utils import log

#: Caption formats we can actually parse. `json3` is the only one carrying
#: per-token offsets — vtt/srt/ttml are line-level, which stage 4 cannot cut on.
CAPTION_FORMAT = "json3"

#: Filename stem inside the job's source directory. Fixed rather than derived
#: from the video title: a YouTube title is caller-influenced text reaching a
#: filesystem path, and `slugify` is ASCII-only so an Arabic title would slug to
#: nothing anyway. The title is kept as metadata, never as a path component.
STEM = "source"


@dataclass
class Fetched:
    """What stage 0 produced. `captions` is None when the video had none we can
    use — the caller then falls back to Whisper and says so."""

    video: Path
    captions: Path | None
    title: str = ""
    duration: float | None = None
    video_id: str = ""
    language: str | None = None


@dataclass
class FetchProgress:
    """One normalised reading from yt-dlp's download loop.

    `total_bytes` is None when nothing knows the size. Callers must render the
    byte count WITHOUT a percentage in that case rather than invent a
    denominator — the same discipline stage 5 follows everywhere else: a
    progress readout that guesses is the report lying about the one thing it
    exists to say.
    """

    downloaded_bytes: int
    #: best available size, which may be yt-dlp's *estimate*. An estimate can be
    #: overshot, so a consumer drawing a bar must clamp its width at 100% while
    #: leaving the byte counts alone — the bytes are measured, the total is not.
    total_bytes: int | None
    #: 1-based position in the sequence of files this fetch downloads. See
    #: `progress_hook` for why a single fetch has more than one.
    file_index: int
    #: this file is done, and this is its closing reading.
    #:
    #: A consumer that throttles its writes MUST let a final reading through. The
    #: last `downloading` event before a file completes is exactly the one a
    #: throttle drops, which would leave a record permanently claiming a finished
    #: download was still at 412 MB of 1.1 GB. A stale number reads as
    #: authoritative, so it is worse than no number at all.
    final: bool = False


def progress_hook(
    on_progress: Callable[[FetchProgress], None],
) -> Callable[[dict], None]:
    """Build a yt-dlp `progress_hooks` entry that reports normalised readings.

    Stateful, and that is the point. **A merged DASH fetch downloads the video
    stream and the audio stream as two SEPARATE files**, so `downloaded_bytes`
    restarts at zero partway through what a user sees as one download. A
    percentage computed straight off the event therefore runs 0->100 twice and
    reads as a bug. `file_index` exists so a consumer can *say* which part is
    running rather than smooth the reset into a fake monotonic number — the
    same call `StageTimeline` makes in refusing to draw a stage that never ran
    as completed.

    **Nothing raised by `on_progress` escapes.** yt-dlp calls this from inside
    its download loop, so an exception here aborts the fetch: a failure to
    *report* progress would cost the video itself. Reported and swallowed.
    """
    seen_filename: str | None = None
    index = 0

    def hook(event: dict) -> None:
        nonlocal seen_filename, index
        try:
            status = event.get("status")
            # `error` is deliberately not reported: the download is failing, and
            # `download` raises SourceNotFetchable for that. A progress reading
            # would only muddy the record on its way out.
            if status not in ("downloading", "finished"):
                return
            filename = event.get("filename")
            if filename != seen_filename:
                seen_filename = filename
                index += 1
            # `or` rather than a None check on purpose: yt-dlp supplies the key
            # with a None value as often as it omits it, and a zero total is
            # just as unusable as a missing one.
            raw_total = event.get("total_bytes") or event.get("total_bytes_estimate")
            on_progress(FetchProgress(
                downloaded_bytes=int(event.get("downloaded_bytes") or 0),
                total_bytes=int(raw_total) if raw_total else None,
                file_index=index,
                final=status == "finished",
            ))
        except Exception as exc:              # noqa: BLE001 — see the docstring
            log(f"progress report failed, download continues: "
                f"{type(exc).__name__}: {exc}")

    return hook


def validate_url(raw: str) -> str:
    """Return `raw` if it names an allowed host over https, else refuse.

    Four separate refusals, and each one closes a real bypass rather than a
    hypothetical:

    - **Scheme.** `file:///etc/passwd` is a URL yt-dlp will happily "download".
      Only https is allowed; not even http, because a downgrade is never needed
      to reach YouTube and allowing it invites a redirect games.
    - **Userinfo.** `https://youtube.com@evil.example/x` has hostname
      `evil.example`, and reads to a human as YouTube. `urlsplit` gets this
      right, but the check is explicit so that a future rewrite onto a different
      parser cannot quietly reintroduce it.
    - **Port.** A port on a YouTube host is never legitimate and is a cheap way
      to aim a request at something else that resolves the same name.
    - **Host.** The allowlist itself, matched case-insensitively on the exact
      hostname. NOT a suffix match: `notyoutube.com` and `youtube.com.evil.net`
      both end in a permitted-looking string, and a `.endswith` check is the
      classic way this boundary is lost.

    The rejected URL is never echoed back — same rule as the validators in
    `api/schemas.py`, and for the same reason: this message reaches an HTTP
    client, and a caller-supplied string in an error body is reflected content."""
    if not isinstance(raw, str) or not raw.strip():
        raise UnsupportedSourceUrl("a source url is required")
    candidate = raw.strip()
    if any(ch in candidate for ch in "\r\n\t\x00"):
        raise UnsupportedSourceUrl("url contains a control character")

    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise UnsupportedSourceUrl("url could not be parsed") from exc

    if parts.scheme.lower() != "https":
        raise UnsupportedSourceUrl("url must use https")
    if "@" in parts.netloc:
        raise UnsupportedSourceUrl("url must not carry userinfo")
    try:
        if parts.port is not None:
            raise UnsupportedSourceUrl("url must not name a port")
    except ValueError as exc:                 # non-numeric port
        raise UnsupportedSourceUrl("url has an invalid port") from exc

    host = (parts.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise UnsupportedSourceUrl(
            f"only these hosts are accepted: {', '.join(sorted(YOUTUBE_HOSTS))}")
    return candidate


def is_url(value: str) -> bool:
    """Whether a CLI positional looks like a URL rather than a path.

    Scheme presence only. Validation is `validate_url`'s job — this just decides
    which of the two the argument is, so a mistyped path does not silently
    become a network fetch (and, more importantly, so `C:\\videos\\x.mov` on
    Windows is not read as scheme `c`)."""
    parts = urlsplit(str(value))
    return len(parts.scheme) > 1 and bool(parts.netloc)


def ytdlp_module():
    """Import yt_dlp, or say which extra is missing.

    Imported inside the function that needs it — CLAUDE.md's import-cost rule —
    which is exactly why the failure needs typing: a lazy import turns a missing
    extra into a raw ModuleNotFoundError from the middle of a running job,
    with no status code and no install hint."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise FetcherNotAvailable(
            "fetching from a url needs yt-dlp — install it with "
            "`pip install -e \".[youtube]\"`, or download the video yourself "
            "and submit it by path or upload"
        ) from exc
    return yt_dlp


def caption_languages(language: str | None) -> list[str]:
    """Which caption tracks to ask for, best first.

    `<lang>-orig` before `<lang>`: on a video whose spoken language is Arabic,
    YouTube publishes `ar-orig` (the ASR output) alongside ~200 machine
    translations, one of which is also called `ar`. They happened to be
    byte-identical on the measured video, but that is a property of that video,
    not a guarantee — asking for the original first means never silently
    transcribing a round-trip translation.

    With no language given, `.*-orig` takes whatever the original was.

    **Every entry is a REGEX, not a glob.** yt-dlp's `orderedSet_from_options`
    runs `re.compile(entry, re.I).fullmatch` over the available track list, so
    the wildcard is `.*` and not `*`. That distinction is not cosmetic: `*-orig`
    raises `re.error("nothing to repeat")` inside `extract_info`, which fails the
    VIDEO download too — captions are requested on the same call — and surfaced
    only as `SourceNotFetchable: could not fetch the video: ValueError`.

    So the entries are compiled HERE, before yt-dlp is handed anything. Checking
    at the boundary that builds them means a malformed tag can never masquerade
    as the far end refusing us, and it fails in the offline suite rather than on
    a worker thread six weeks later.

    Two checks, and they catch genuinely different things:

    - The caller's tag must BE a tag. `.*` compiles perfectly and fullmatches
      every published track, so stage 2' would be handed one of ~200 machine
      translations instead of the original ASR — wrong captions, wrong cut
      points, and nothing in any log to say so. A crash is the loud failure
      here; this is the quiet one, and it is the worse of the two.
    - The entries we then BUILD must compile. That is not redundant with the
      first check: `.*-orig` is ours, not the caller's, and the whole reason
      this function is documented at such length is that a previous version of
      that literal did not compile."""
    if language is not None and not re.fullmatch(LANGUAGE_TAG_PATTERN, language):
        raise CaptionLanguageInvalid(
            "the caption language must be a plain language tag — letters, "
            "digits and hyphens, as in 'ar' or 'pt-BR'")

    langs = [".*-orig"] if not language else [f"{language}-orig", language]
    for entry in langs:
        try:
            re.compile(entry)
        except re.error as exc:                   # pragma: no cover — belt and braces
            raise CaptionLanguageInvalid(
                "the caption language must be a plain language tag — letters, "
                "digits and hyphens, as in 'ar' or 'pt-BR'"
            ) from exc
    return langs


def download(url: str, dest: Path, *, language: str | None = None,
             want_captions: bool = True,
             on_progress: Callable[[FetchProgress], None] | None = None) -> Fetched:
    """Fetch the video, and its caption track when asked, into `dest`.

    Validates first — `download` is reachable from the CLI as well as the API,
    so it cannot rely on a router having checked.

    `on_progress` is called with a `FetchProgress` as bytes land. It is passed
    IN rather than reached for, so this module still knows nothing about jobs or
    a store: the layer arrows say `pipeline` may not import `jobs`, and a
    progress feature is not a reason to bend that."""
    url = validate_url(url)
    yt_dlp = ytdlp_module()
    dest.mkdir(parents=True, exist_ok=True)

    options = {
        "outtmpl": str(dest / f"{STEM}.%(ext)s"),
        # A URL naming a playlist would otherwise fetch every video in it —
        # unbounded work from one request, which is a denial of service dressed
        # as a convenience.
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": False,
        "writeautomaticsub": want_captions,
        "writesubtitles": want_captions,
        "subtitleslangs": caption_languages(language),
        "subtitlesformat": CAPTION_FORMAT,
        "merge_output_format": "mp4",
    }
    if on_progress is not None:
        options["progress_hooks"] = [progress_hook(on_progress)]

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except FetcherNotAvailable:
        raise
    except Exception as exc:                  # noqa: BLE001 — yt-dlp raises many types
        # The URL passed our allowlist, so this SHOULD be the far end saying no:
        # private, removed, region-locked, rate-limited. Distinct from a refusal.
        #
        # It once wasn't, and the comment claiming otherwise cost three jobs and
        # an afternoon: a malformed `subtitleslangs` of our own making raised
        # here too, and reporting only the type name left "ValueError" as the
        # entire diagnosis. That is why the options are validated as they are
        # BUILT, above and in `caption_languages`, rather than being left for
        # this clause to mischaracterise. Keep new option-shape checks up there.
        #
        # The message stays type-only on purpose: yt-dlp puts the URL in its own
        # error text, and this string reaches an HTTP client. The full traceback
        # is logged server-side, which is where it belongs.
        raise SourceNotFetchable(f"could not fetch the video: {type(exc).__name__}") from exc

    video = _one_media_file(dest)
    if video is None:
        raise SourceNotFetchable("the fetch reported success but produced no video file")

    captions = _one_caption_file(dest) if want_captions else None
    if want_captions and captions is None:
        log("stage 0: no usable caption track — transcription will run")

    return Fetched(
        video=video,
        captions=captions,
        title=str(info.get("title") or ""),
        duration=info.get("duration"),
        video_id=str(info.get("id") or ""),
        language=info.get("language") or language,
    )


def _one_media_file(dest: Path) -> Path | None:
    """The downloaded media, whatever container it landed in."""
    candidates = [p for p in sorted(dest.glob(f"{STEM}.*"))
                  if p.suffix.lower() not in (".json3", ".json", ".vtt", ".srt", ".ttml")]
    return candidates[0] if candidates else None


def _one_caption_file(dest: Path) -> Path | None:
    """The caption track, preferring an `-orig` one.

    yt-dlp names these `source.<lang>.json3`, so the `-orig` preference in
    `caption_languages` has to be re-applied here: asking for two tracks can
    return both, and the original is the one to parse."""
    tracks = sorted(dest.glob(f"{STEM}.*.{CAPTION_FORMAT}"))
    if not tracks:
        return None
    for track in tracks:
        if "-orig" in track.name:
            return track
    return tracks[0]

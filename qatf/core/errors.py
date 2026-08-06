"""Domain errors.

Every failure this project raises on purpose is a `QatfError`. Anything else
escaping the pipeline is a bug, and the API surfaces it as a 500 rather than
pretending it was a client mistake.

`status_code` exists so `qatf.api` can map these without a chain of isinstance
checks. The pipeline itself never imports HTTP anything.
"""

from __future__ import annotations


class QatfError(Exception):
    """Base for deliberate failures."""

    status_code: int = 500


# -- environment -----------------------------------------------------------

class FFmpegNotFound(QatfError):
    """ffmpeg is not on PATH."""

    status_code = 503


class CommandFailed(QatfError):
    """A subprocess exited non-zero. Message carries the tail of its stderr."""


# -- stage 2 ---------------------------------------------------------------

class NoSpeechFound(QatfError):
    """Whisper returned no words. Nothing to clip."""

    status_code = 422


class SeedTooLong(QatfError):
    """The vocabulary/prompt would leave Whisper no context to decode into."""

    status_code = 422


# -- stage 3 ---------------------------------------------------------------

class ModelResponseError(QatfError):
    """The model returned something that was not the requested JSON."""

    status_code = 502


class ProviderNotConfigured(QatfError):
    """Unknown provider key, or a hosted provider with no API key."""

    status_code = 503


class ModelRefused(QatfError):
    """The provider's safety classifiers declined the request.

    Distinct from a transport failure: the call succeeded, the model chose not
    to answer. Retrying the same prompt on the same provider will not help."""

    status_code = 502


class TranscriptTooLong(QatfError):
    """The transcript will not fit the provider's context window."""

    status_code = 413


# -- plan / render ---------------------------------------------------------

class EmptyPlan(QatfError):
    """Asked to render a job with no clips."""

    status_code = 409


class NoTranscript(QatfError):
    """Asked for something that needs a transcript before one exists."""

    status_code = 409


class TranscriptStructureChanged(QatfError):
    """A submitted transcript changed something other than word text.

    Word timings come from the audio and are what every cut point is snapped to.
    Corrections may change what a caption reads; they may never move a cut. This
    is the core invariant refusing at the boundary rather than downstream."""

    status_code = 422


# -- source resolution -----------------------------------------------------

class SourceNotFound(QatfError):
    status_code = 404


class SourceOutsideMediaRoot(QatfError):
    """A caller-supplied path escaped the media root. Security boundary."""

    status_code = 403


class UploadTooLarge(QatfError):
    status_code = 413


class UnsupportedSource(QatfError):
    status_code = 415

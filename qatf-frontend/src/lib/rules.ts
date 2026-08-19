// Client-side mirrors of server rules — instant feedback only. The server
// stays the authority; nothing here may be the ONLY place a rule lives.
import type { JobOptions, WordModel, ClipModel } from "../api/types";

export type FieldErrors = Record<string, string>;

const LANGUAGE_RE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/;
/** schemas.py caps `language` at 32 characters as well as matching the pattern.
 * A long chain of subtags satisfies the regex alone. */
const LANGUAGE_MAX = 32;
/** `parse_resolution` strips and lowercases before matching, so `  4K ` is
 * valid server-side. A mirror that refuses it rejects input the server accepts,
 * which is the one direction a mirror is allowed to be wrong in but still
 * makes the UI feel broken. Normalise the same way before testing. */
const RESOLUTION_RE = /^(source|1080p|1440p|4k|\d{2,5}x\d{2,5})$/;

export function validateOptions(o: JobOptions): FieldErrors {
  const errors: FieldErrors = {};
  if (o.clips < 1 || o.clips > 50) errors.clips = "between 1 and 50";
  if (o.min_len < 1 || o.min_len > 600) errors.min_len = "between 1 and 600 seconds";
  if (o.max_len < 1 || o.max_len > 600) errors.max_len = "between 1 and 600 seconds";
  if (!errors.min_len && !errors.max_len && o.min_len > o.max_len) {
    errors.min_len = "min_len must be <= max_len";
  }
  if (o.crf < 0 || o.crf > 51) errors.crf = "between 0 and 51";
  if (o.per_line < 1 || o.per_line > 8) errors.per_line = "between 1 and 8";
  if (o.language !== null
      && (!LANGUAGE_RE.test(o.language) || o.language.length > LANGUAGE_MAX)) {
    errors.language = "a language tag like ar or en-US";
  }
  if (!RESOLUTION_RE.test(o.resolution.trim().toLowerCase())) {
    errors.resolution = "source, 1080p, 1440p, 4k, or WxH like 1080x1920";
  }
  return errors;
}

/** Mirror of PUT /jobs/{id}/transcript's contract: only text may differ.
 * Timings come from the audio and are what every cut is snapped to. */
export function transcriptEditGuard(
  original: WordModel[],
  edited: WordModel[],
): string | null {
  if (edited.length !== original.length) {
    return `word count changed (${original.length} -> ${edited.length}) — ` +
      "to split a word, put both words in its text instead";
  }
  for (let i = 0; i < original.length; i++) {
    if (edited[i].start !== original[i].start || edited[i].end !== original[i].end) {
      return `timing changed at word ${i + 1} — timings are never editable`;
    }
  }
  return null;
}

/** Mirror of core.constants.DURATION_SLACK — the absolute allowance
 * within_duration grants either side of the requested bounds. */
export const DURATION_SLACK = 2.0;

/** Warn when a hand-edited clip would fall outside what the pipeline keeps.
 * Absolute slack, not proportional — that is a deliberate backend decision. */
export function durationWarning(clip: ClipModel, maxLen: number): string | null {
  const duration = clip.end - clip.start;
  if (duration <= 0) return "end must be after start";
  if (duration > maxLen + DURATION_SLACK) {
    return `${duration.toFixed(1)}s exceeds max_len ${maxLen}s + ${DURATION_SLACK}s slack — ` +
      "the render keeps it, but Shorts may reject it";
  }
  return null;
}

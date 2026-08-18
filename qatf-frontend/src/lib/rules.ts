// Client-side mirrors of server rules — instant feedback only. The server
// stays the authority; nothing here may be the ONLY place a rule lives.
import type { JobOptions, WordModel } from "../api/types";

export type FieldErrors = Record<string, string>;

const LANGUAGE_RE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/;
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
  if (o.language !== null && !LANGUAGE_RE.test(o.language)) {
    errors.language = "a language tag like ar or en-US";
  }
  if (!RESOLUTION_RE.test(o.resolution)) {
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

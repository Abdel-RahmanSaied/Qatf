import type { JobResponse } from "../api/types";

type Named = Pick<JobResponse, "id" | "video"> &
  Partial<Pick<JobResponse, "source" | "url">>;

/** A name a person recognises for a job: the video's file name when there is
 * one, else the job id. `video` may be a server path, so only the last segment
 * is shown. Pure, so it lives here beside the other tested helpers.
 *
 * A `youtube` job's `video` never names anything a person chose: it is the
 * server's `<pending fetch>` placeholder until stage 0 finishes, and then
 * `<data>/<id>/source/source.<ext>` — every URL job would be titled
 * "source.mp4". So a URL job is named from its `url` instead. */
export function videoName(job: Named): string {
  if (job.source === "youtube" && job.url) {
    return youtubeName(job.url, job.id);
  }
  const name = (job.video ?? "").split(/[\\/]/).filter(Boolean).pop();
  return name && name.trim() !== "" ? name : job.id;
}

/** The title for a YouTube job, from the URL the caller submitted.
 *
 * The URL has already passed the server's allowlist (`fetch.validate_url`), so
 * its host is youtube.com / www.youtube.com / m.youtube.com / youtu.be. The
 * shapes that reach here:
 *   https://youtu.be/Wal49rZjHMM?si=9PvHGJQCwftMQjWC
 *   https://www.youtube.com/watch?v=Wal49rZjHMM&t=42
 *   https://www.youtube.com/shorts/Wal49rZjHMM
 *
 * The API exposes no video title — only the URL — so whatever this returns is
 * all a person has to tell two URL jobs apart in the jobs list. */
function youtubeName(url: string, id: string): string {
  // TODO(you): decide what a YouTube job is called. 5-10 lines.
  void url;
  return id;
}

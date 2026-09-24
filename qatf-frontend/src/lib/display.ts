import type { JobResponse } from "../api/types";

/** A name a person recognises for a job: the video's file name when there is
 * one, else the job id. `video` may be a server path, so only the last segment
 * is shown. Pure, so it lives here beside the other tested helpers. */
export function videoName(job: Pick<JobResponse, "id" | "video">): string {
  const name = (job.video ?? "").split(/[\\/]/).filter(Boolean).pop();
  return name && name.trim() !== "" ? name : job.id;
}

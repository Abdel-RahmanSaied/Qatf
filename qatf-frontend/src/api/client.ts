import type {
  ClipModel, ClipOutput, Health, JobOptions, JobResponse, JobState,
  TranscriptResponse, WordModel,
} from "./types";

const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Normalise any error body to one string. Handles ErrorResponse ({detail: str})
 * and FastAPI 422s ({detail: [{loc, msg}, ...]}); anything else -> fallback. */
export function detailFrom(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const parts = detail.map((item: unknown) => {
        if (item && typeof item === "object") {
          const { loc, msg } = item as { loc?: unknown; msg?: unknown };
          const where = Array.isArray(loc) ? loc.join(".") : "";
          const reason = typeof msg === "string" ? msg : JSON.stringify(item);
          return where ? `${where}: ${reason}` : reason;
        }
        return String(item);
      });
      if (parts.length) return parts.join("; ");
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_BASE + path, init);
  } catch {
    throw new ApiError(0, "cannot reach the server");
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null; // non-JSON body (proxy error page); fall back to status
    }
  }
  if (!res.ok) throw new ApiError(res.status, detailFrom(body, `HTTP ${res.status}`));
  return body as T;
}

function jsonInit(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

export function health(): Promise<Health> {
  return request<Health>("/healthz");
}

export async function listJobs(state?: JobState): Promise<JobResponse[]> {
  const query = state ? `?state=${state}` : "";
  const { jobs } = await request<{ jobs: JobResponse[] }>(`/jobs${query}`);
  return jobs;
}

export function getJob(id: string): Promise<JobResponse> {
  return request<JobResponse>(`/jobs/${id}`);
}

export function createJobFromPath(path: string, options: JobOptions): Promise<JobResponse> {
  return request<JobResponse>("/jobs", jsonInit("POST", { path, ...options }));
}

export function createJobFromUrl(url: string, options: JobOptions): Promise<JobResponse> {
  return request<JobResponse>("/jobs/url", jsonInit("POST", { url, ...options }));
}

/** Multipart upload via XHR — fetch still has no upload-progress events. */
export function uploadJob(
  file: File,
  options: JobOptions,
  onProgress: (frac: number) => void,
): Promise<JobResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/jobs/upload`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as JobResponse);
      } else {
        reject(new ApiError(xhr.status, detailFrom(body, `HTTP ${xhr.status}`)));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "cannot reach the server"));
    const form = new FormData();
    form.append("file", file);
    form.append("options", JSON.stringify(options));
    xhr.send(form);
  });
}

export function cancelJob(id: string): Promise<JobResponse> {
  return request<JobResponse>(`/jobs/${id}/cancel`, { method: "POST" });
}

export function deleteJob(id: string): Promise<void> {
  return request<void>(`/jobs/${id}`, { method: "DELETE" });
}

export function getTranscript(id: string): Promise<TranscriptResponse> {
  return request<TranscriptResponse>(`/jobs/${id}/transcript`);
}

export function putTranscript(id: string, words: WordModel[]): Promise<TranscriptResponse> {
  return request<TranscriptResponse>(`/jobs/${id}/transcript`, jsonInit("PUT", { words }));
}

/** snap is ALWAYS true — a typed second is a semantic guess, and the core
 * invariant says acoustic boundaries come from Whisper. No UI toggle. */
export function putPlan(id: string, clips: ClipModel[]): Promise<ClipModel[]> {
  return request<ClipModel[]>(`/jobs/${id}/plan`, jsonInit("PUT", { clips, snap: true }));
}

export function renderJob(id: string): Promise<JobResponse> {
  return request<JobResponse>(`/jobs/${id}/render`, { method: "POST" });
}

/** ClipOutput.url is API-root-relative; the browser needs the /api prefix. */
export function clipUrl(output: ClipOutput): string {
  return API_BASE + output.url;
}

/** Stream a clip into memory, reporting bytes as they land.
 *
 * The mirror of `uploadJob`, and it needs the OPPOSITE mechanism. That function
 * drops to XHR because fetch has no upload-progress events; fetch does expose
 * the download side, as a ReadableStream on `res.body`, so there is no reason
 * to reach for XHR here.
 *
 * `total` is null when nothing knows the size, and callers must then render
 * bytes WITHOUT a percentage rather than invent a denominator — the same rule
 * the harvest strip follows in refusing to assume a source duration. A bar that
 * guesses its own total is the UI lying about the one thing it exists to report.
 *
 * The whole clip is buffered before it is handed back. A clip is bounded by
 * `--max-len`, so this is tens of MB; the streaming alternative
 * (`showSaveFilePicker`) is Chromium-only and not worth the split path.
 */
export async function downloadClip(
  output: ClipOutput,
  onProgress: (loaded: number, total: number | null) => void,
): Promise<Blob> {
  let res: Response;
  try {
    res = await fetch(clipUrl(output));
  } catch {
    throw new ApiError(0, "cannot reach the server");
  }
  if (!res.ok) {
    const text = await res.text();
    let body: unknown = null;
    try {
      body = JSON.parse(text);
    } catch {
      body = null; // non-JSON body (proxy error page); fall back to status
    }
    throw new ApiError(res.status, detailFrom(body, `HTTP ${res.status}`));
  }

  // Content-Length is what is actually coming over THIS wire, so it wins over
  // the recorded size, which was read when the job record was last fetched.
  // `.get` returns null when absent and Number(null) is 0, so both fall through.
  const declared = Number(res.headers.get("Content-Length"));
  const total = declared > 0 ? declared
    : output.size_bytes > 0 ? output.size_bytes
    : null;

  const reader = res.body?.getReader();
  if (!reader) return res.blob(); // no stream to meter; the file still arrives

  const chunks: Uint8Array[] = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    onProgress(loaded, total);
  }
  return new Blob(chunks, { type: res.headers.get("Content-Type") ?? "" });
}

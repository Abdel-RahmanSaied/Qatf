import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, detailFrom, downloadClip, getJob, listJobs } from "./client";
import type { ClipOutput } from "./types";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("detailFrom", () => {
  it("uses a string detail verbatim", () => {
    expect(detailFrom({ detail: "job is running" }, "HTTP 409"))
      .toBe("job is running");
  });

  it("flattens FastAPI 422 detail lists to location: reason", () => {
    const body = {
      detail: [
        { loc: ["body", "min_len"], msg: "min_len must be <= max_len" },
        { loc: ["body", "language"], msg: "string does not match pattern" },
      ],
    };
    expect(detailFrom(body, "HTTP 422")).toBe(
      "body.min_len: min_len must be <= max_len; " +
      "body.language: string does not match pattern",
    );
  });

  it("falls back when the body is not an ErrorResponse", () => {
    expect(detailFrom(null, "HTTP 500")).toBe("HTTP 500");
    expect(detailFrom({ weird: true }, "HTTP 500")).toBe("HTTP 500");
  });
});

describe("request layer", () => {
  it("unwraps { jobs } from GET /jobs and prefixes /api", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { jobs: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const jobs = await listJobs();
    expect(jobs).toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith("/api/jobs", undefined);
  });

  it("throws ApiError with the server's detail on a non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse(404, { detail: "no such job" })));
    const err = await getJob("nope").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("no such job");
  });

  it("maps a network failure to ApiError status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const err = await getJob("x").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(0);
    expect((err as ApiError).message).toBe("cannot reach the server");
  });

  it("survives a non-JSON error body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("<html>bad gateway</html>", { status: 502 })));
    const err = await getJob("x").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toBe("HTTP 502");
  });
});

/** A response whose body arrives in pieces, like a real clip download. */
function streamResponse(chunks: Uint8Array[], init?: ResponseInit): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  return new Response(stream, init);
}

const CLIP: ClipOutput = {
  name: "01-clip.mp4",
  size_bytes: 10,
  url: "/jobs/j1/clips/01-clip.mp4",
};

describe("downloadClip", () => {
  it("reports bytes as they arrive, against the Content-Length total", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(
      [new Uint8Array(4), new Uint8Array(6)],
      { headers: { "Content-Length": "10" } },
    )));
    const seen: Array<[number, number | null]> = [];
    await downloadClip(CLIP, (loaded, total) => seen.push([loaded, total]));
    expect(seen).toEqual([[4, 10], [10, 10]]);
  });

  it("falls back to the recorded size_bytes when Content-Length is absent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      streamResponse([new Uint8Array(4)])));
    const seen: Array<[number, number | null]> = [];
    await downloadClip(CLIP, (loaded, total) => seen.push([loaded, total]));
    expect(seen).toEqual([[4, 10]]);
  });

  it("reports a null total rather than inventing one when no size is known", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      streamResponse([new Uint8Array(4)])));
    const seen: Array<[number, number | null]> = [];
    await downloadClip({ ...CLIP, size_bytes: 0 },
      (loaded, total) => seen.push([loaded, total]));
    expect(seen).toEqual([[4, null]]);
  });

  it("resolves a blob holding the whole body, in order", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(
      [Uint8Array.from([1, 2]), Uint8Array.from([3, 4, 5])])));
    const blob = await downloadClip(CLIP, () => {});
    expect(blob.size).toBe(5);
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([1, 2, 3, 4, 5]);
  });

  it("prefixes /api on the clip url", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamResponse([new Uint8Array(1)]));
    vi.stubGlobal("fetch", fetchMock);
    await downloadClip(CLIP, () => {});
    expect(fetchMock).toHaveBeenCalledWith("/api/jobs/j1/clips/01-clip.mp4");
  });

  it("throws ApiError with the server's detail on a non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse(404, { detail: "no such clip" })));
    const err = await downloadClip(CLIP, () => {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("no such clip");
  });

  it("maps a network failure to ApiError status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const err = await downloadClip(CLIP, () => {}).catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(0);
    expect((err as ApiError).message).toBe("cannot reach the server");
  });
});

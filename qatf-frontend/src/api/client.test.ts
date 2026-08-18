import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, detailFrom, getJob, listJobs } from "./client";

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

import { describe, expect, it } from "vitest";
import {
  describeFetch, formatAge, formatBytes, formatSeconds, summariseRange,
  STAGES, stageIndex,
} from "./format";

describe("formatAge", () => {
  const now = new Date("2026-08-18T12:00:00Z");
  it("says just now under a minute", () => {
    expect(formatAge("2026-08-18T11:59:40Z", now)).toBe("just now");
  });
  it("uses minutes, hours, days", () => {
    expect(formatAge("2026-08-18T11:45:00Z", now)).toBe("15m ago");
    expect(formatAge("2026-08-18T09:00:00Z", now)).toBe("3h ago");
    expect(formatAge("2026-08-15T12:00:00Z", now)).toBe("3d ago");
  });
});

describe("formatBytes", () => {
  it("scales units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(18_432_119)).toBe("17.6 MB");
    expect(formatBytes(2_147_483_648)).toBe("2.0 GB");
  });
});

describe("formatSeconds", () => {
  it("renders M:SS.cc", () => {
    expect(formatSeconds(184.32)).toBe("3:04.32");
    expect(formatSeconds(59.999)).toBe("1:00.00"); // carry, same trap as ts_ass
  });
});

describe("stageIndex", () => {
  it("orders the happy path", () => {
    expect(stageIndex("queued")).toBe(0);
    expect(stageIndex("transcribing")).toBe(3);
    expect(stageIndex("rendering")).toBe(6);
  });
  it("puts done past the last stage", () => {
    expect(stageIndex("done")).toBe(STAGES.length);
  });
  it("returns -1 for states off the happy path", () => {
    expect(stageIndex("failed")).toBe(-1);
    expect(stageIndex("cancelled")).toBe(-1);
  });
  it("covers every stage exactly once", () => {
    expect(new Set(STAGES).size).toBe(STAGES.length);
  });
});

describe("summariseRange", () => {
  const clip = (out: "short" | "long" | null) => ({ out_of_range: out });

  it("counts nothing when the whole plan is in range", () => {
    expect(summariseRange([clip(null), clip(null)])).toEqual({ short: 0, long: 0 });
  });

  // The measured case: qwen3-235b returned eight clips, six of them two
  // transcript blocks long (~24s) against a min_len of 30. All eight are in the
  // plan; six carry the label.
  it("counts the 24s case as six short, none long", () => {
    const plan = [clip(null), clip(null), ...Array(6).fill(clip("short"))];
    expect(summariseRange(plan)).toEqual({ short: 6, long: 0 });
  });

  it("separates the two ends", () => {
    expect(summariseRange([clip("short"), clip("long"), clip(null), clip("long")]))
      .toEqual({ short: 1, long: 2 });
  });

  // It counts the SERVER'S label. Re-deriving from duration here would apply no
  // DURATION_SLACK and flag clips the server considers fine.
  it("ignores duration entirely and trusts the label", () => {
    const plan = [{ out_of_range: null, duration: 3 } as never];
    expect(summariseRange(plan)).toEqual({ short: 0, long: 0 });
  });
});

// RECONSTRUCTED alongside describeFetch itself — these pin the contract
// FetchProgress.tsx depends on, not necessarily the original assertions.
describe("describeFetch", () => {
  it("has no percent when the total is unknown — a bar needs a denominator", () => {
    const r = describeFetch({ downloaded_bytes: 1024, total_bytes: null, file_index: 0 });
    expect(r.percent).toBeNull();
    expect(r.text).toBe("1.0 KB");
  });

  it("reports downloaded over total when the total is known", () => {
    const r = describeFetch({
      downloaded_bytes: 512 * 1024 ** 2, total_bytes: 1024 ** 3, file_index: 0,
    });
    expect(r.percent).toBe(50);
    expect(r.text).toBe("512.0 MB / 1.0 GB");
  });

  // yt-dlp's total is an estimate it can overshoot. Clamp the BAR at 100 and
  // leave the byte counts alone: the bytes are measured, the total may not be.
  it("clamps the bar at 100% without touching the byte counts", () => {
    const r = describeFetch({
      downloaded_bytes: 1200, total_bytes: 1000, file_index: 0,
    });
    expect(r.percent).toBe(100);
    expect(r.text).toBe("1.2 KB / 1000 B");
  });

  // A merged DASH fetch restarts downloaded_bytes at zero for the audio
  // stream. A counter that visibly resets with nothing explaining it is a bug
  // report waiting to happen.
  it("names the file once a merged fetch moves to the second stream", () => {
    const r = describeFetch({ downloaded_bytes: 0, total_bytes: 100, file_index: 1 });
    expect(r.text).toBe("0 B / 100 B · file 2");
  });
});

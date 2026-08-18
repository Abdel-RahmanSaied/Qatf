import { describe, expect, it } from "vitest";
import { formatAge, formatBytes, formatSeconds } from "./format";

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

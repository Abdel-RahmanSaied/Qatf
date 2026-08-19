import { describe, expect, it } from "vitest";
import { DEFAULT_OPTIONS } from "../api/types";
import { validateOptions, transcriptEditGuard, DURATION_SLACK, durationWarning } from "./rules";
import type { WordModel, ClipModel } from "../api/types";

describe("validateOptions", () => {
  it("accepts the defaults", () => {
    expect(validateOptions(DEFAULT_OPTIONS)).toEqual({});
  });

  it("mirrors min_len <= max_len", () => {
    const errors = validateOptions({ ...DEFAULT_OPTIONS, min_len: 60, max_len: 30 });
    expect(errors.min_len).toBeTruthy();
  });

  it("mirrors the language tag pattern (it is part of a cache FILENAME server-side)", () => {
    expect(validateOptions({ ...DEFAULT_OPTIONS, language: "ar" })).toEqual({});
    expect(validateOptions({ ...DEFAULT_OPTIONS, language: "en-US" })).toEqual({});
    expect(validateOptions({ ...DEFAULT_OPTIONS, language: "../x" }).language).toBeTruthy();
  });

  it("bounds clips, crf and per_line", () => {
    expect(validateOptions({ ...DEFAULT_OPTIONS, clips: 0 }).clips).toBeTruthy();
    expect(validateOptions({ ...DEFAULT_OPTIONS, clips: 51 }).clips).toBeTruthy();
    expect(validateOptions({ ...DEFAULT_OPTIONS, crf: 52 }).crf).toBeTruthy();
    expect(validateOptions({ ...DEFAULT_OPTIONS, per_line: 9 }).per_line).toBeTruthy();
  });

  it("accepts every resolution form the backend parses", () => {
    for (const r of ["source", "1080p", "1440p", "4k", "1080x1920", "1214x2160"]) {
      expect(validateOptions({ ...DEFAULT_OPTIONS, resolution: r })).toEqual({});
    }
    expect(validateOptions({ ...DEFAULT_OPTIONS, resolution: "huge" }).resolution).toBeTruthy();
  });

  it("normalises resolution the way parse_resolution does", () => {
    // The server strips and lowercases before matching, so refusing these
    // would reject input it accepts.
    for (const r of ["4K", " 1080p ", "SOURCE", "1080X1920"]) {
      expect(validateOptions({ ...DEFAULT_OPTIONS, resolution: r })).toEqual({});
    }
  });

  it("enforces the language max_length the schema also enforces", () => {
    // Matches LANGUAGE_RE (subtag chain) but exceeds the 32-char cap.
    const long = "en-AAAAAAAA-BBBBBBBB-CCCCCCCC-DDDDDDDD";
    expect(long.length).toBeGreaterThan(32);
    expect(validateOptions({ ...DEFAULT_OPTIONS, language: long }).language).toBeTruthy();
  });
});

describe("transcriptEditGuard", () => {
  const original: WordModel[] = [
    { text: "هو", start: 204.11, end: 204.29 },
    { text: "من", start: 204.29, end: 204.58 },
  ];

  it("allows a text-only correction", () => {
    const edited = [original[0], { ...original[1], text: "مين" }];
    expect(transcriptEditGuard(original, edited)).toBeNull();
  });

  it("refuses a changed word count", () => {
    expect(transcriptEditGuard(original, [original[0]])).toMatch(/word count/);
  });

  it("refuses a retiming", () => {
    const edited = [original[0], { ...original[1], start: 204.30 }];
    expect(transcriptEditGuard(original, edited)).toMatch(/timing/);
  });
});

describe("durationWarning", () => {
  const clip = (start: number, end: number): ClipModel =>
    ({ start, end, title: "t", hook: "", why: "", score: 0 });

  it("accepts a clip inside max_len + slack", () => {
    expect(durationWarning(clip(0, 52), 52)).toBeNull();
    expect(durationWarning(clip(0, 52 + DURATION_SLACK), 52)).toBeNull();
  });

  it("warns beyond max_len + slack — the same absolute rule within_duration applies", () => {
    expect(durationWarning(clip(0, 55), 52)).toMatch(/max_len/);
  });

  it("flags a non-positive duration as an error", () => {
    expect(durationWarning(clip(30, 30), 52)).toMatch(/end/);
  });
});

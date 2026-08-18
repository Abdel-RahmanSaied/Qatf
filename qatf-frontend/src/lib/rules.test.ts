import { describe, expect, it } from "vitest";
import { DEFAULT_OPTIONS } from "../api/types";
import { validateOptions } from "./rules";

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
});

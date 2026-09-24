import { describe, expect, it } from "vitest";
import { videoName } from "./display";

describe("videoName", () => {
  it("uses the file name from a path", () => {
    expect(videoName({ id: "a1", video: "/data/media/talks/keynote.mov" })).toBe("keynote.mov");
    expect(videoName({ id: "a1", video: "C:\\media\\talk.mp4" })).toBe("talk.mp4");
  });

  it("falls back to the id when there is no video name", () => {
    expect(videoName({ id: "a1", video: "" })).toBe("a1");
    expect(videoName({ id: "a1", video: "/" })).toBe("a1");
  });
});

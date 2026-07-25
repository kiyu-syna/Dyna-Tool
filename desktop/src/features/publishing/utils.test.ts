import { describe, expect, it } from "vitest";
import type { Job } from "../../shared/types";
import { directoryName, fileName, jobTone, parseScheduleTimes, sourceFileName } from "./utils";

describe("publishing utilities", () => {
  it("normalizes and sorts valid schedule times", () => {
    expect(parseScheduleTimes("19:00, 09:00; 19:00 25:00 9:00")).toEqual(["09:00", "19:00"]);
  });

  it("separates Windows file paths", () => {
    expect(fileName("C:\\videos\\clip.mp4")).toBe("clip.mp4");
    expect(directoryName("C:\\videos\\clip.mp4")).toBe("C:\\videos");
  });

  it("maps queue states to visual tones", () => {
    expect(jobTone("completed")).toBe("success");
    expect(jobTone("failed_upload")).toBe("danger");
    expect(jobTone("uploading")).toBe("info");
    expect(jobTone("scheduled")).toBe("warning");
  });

  it("prefers the source filename embedded in a job label", () => {
    const job = { source_label: "TikTok · clip.mp4", video_id: "123" } as Job;
    expect(sourceFileName(job)).toBe("clip.mp4");
  });
});

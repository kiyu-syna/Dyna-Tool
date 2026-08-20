import { describe, expect, it } from "vitest";
import type { Job } from "../../shared/types";
import { buildRandomSchedule, directoryName, fileName, jobTone, parseScheduleTimes, sourceFileName } from "./utils";

describe("publishing utilities", () => {
  it("normalizes and sorts valid schedule times", () => {
    expect(parseScheduleTimes("19:00, 09:00; 19:00 25:00 9:00")).toEqual(["09:00", "19:00"]);
  });

  it("creates random daily times inside the requested range", () => {
    const values = buildRandomSchedule(5, "2030-01-02", 2, "09:00", "10:00", {
      now: new Date("2030-01-01T00:00:00").getTime(),
      random: () => 0.42,
    });

    expect(values).toHaveLength(5);
    expect(
      values.every((value) => {
        const time = value.slice(11, 16);
        return time >= "09:00" && time <= "10:00";
      }),
    ).toBe(true);
    const counts = values.reduce<Record<string, number>>((result, value) => {
      result[value.slice(0, 10)] = (result[value.slice(0, 10)] || 0) + 1;
      return result;
    }, {});
    expect(Math.max(...Object.values(counts))).toBeLessThanOrEqual(2);
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

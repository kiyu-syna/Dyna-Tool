import { describe, expect, it } from "vitest";
import { normalizeTikTokUsername, sourceIdentity, tiktokInputValue } from "./utils";

describe("profile source utilities", () => {
  it("normalizes TikTok handles and profile URLs", () => {
    expect(normalizeTikTokUsername("@creator")).toBe("creator");
    expect(normalizeTikTokUsername("https://www.tiktok.com/@creator/video/123")).toBe("creator");
  });

  it("creates stable source identities", () => {
    expect(sourceIdentity({ platform: "tiktok", unique_id: "@Creator" })).toBe("tiktok:creator");
    expect(sourceIdentity({ platform: "douyin", sec_uid: "MS4wLjAB" })).toBe("douyin:ms4wljab");
  });

  it("formats TikTok input with one @ prefix", () => {
    expect(tiktokInputValue({ platform: "tiktok", unique_id: "creator" })).toBe("@creator");
    expect(tiktokInputValue({ platform: "tiktok", unique_id: "@creator" })).toBe("@creator");
  });
});

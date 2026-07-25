import { describe, expect, it } from "vitest";

import { messages } from "./messages";

describe("shared translation catalog", () => {
  it("contains a non-empty translation for every supported base language", () => {
    for (const [id, message] of Object.entries(messages)) {
      expect(message.vi.trim(), `${id}.vi`).not.toBe("");
      expect(message.en.trim(), `${id}.en`).not.toBe("");
      expect(message.zh.trim(), `${id}.zh`).not.toBe("");
    }
  });
});

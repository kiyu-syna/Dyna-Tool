import { beforeEach, describe, expect, it } from "vitest";
import { ASSISTANT_STORAGE_KEY, MAX_SAVED_MESSAGES, assistantDisplayText, loadMessages } from "./model";

describe("assistant model utilities", () => {
  beforeEach(() => localStorage.clear());

  it("unwraps JSON and fenced assistant replies", () => {
    expect(assistantDisplayText('{"reply":"Xin chào"}')).toBe("Xin chào");
    expect(assistantDisplayText('```json\n{"reply":"Ready"}\n```')).toBe("Ready");
    expect(assistantDisplayText("plain text")).toBe("plain text");
  });

  it("loads only valid messages and enforces the saved-message limit", () => {
    const messages = Array.from({ length: MAX_SAVED_MESSAGES + 5 }, (_, index) => ({
      id: String(index),
      role: index % 2 ? "assistant" : "user",
      content: `message-${index}`,
      createdAt: "2026-07-24T00:00:00.000Z",
    }));
    localStorage.setItem(ASSISTANT_STORAGE_KEY, JSON.stringify([{ role: "system", content: "ignored" }, ...messages]));

    const loaded = loadMessages();
    expect(loaded).toHaveLength(MAX_SAVED_MESSAGES);
    expect(loaded[0].content).toBe("message-5");
  });
});

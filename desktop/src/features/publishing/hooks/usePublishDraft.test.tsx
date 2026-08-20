import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePublishDraft } from "./usePublishDraft";

const originalBridge = window.dyna;
const l = (_vi: string, en: string) => en;

function localDateValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

describe("usePublishDraft", () => {
  afterEach(() => {
    Object.defineProperty(window, "dyna", { configurable: true, value: originalBridge });
  });

  it("selects local videos, removes duplicates, validates captions, and creates a bulk schedule", async () => {
    Object.defineProperty(window, "dyna", {
      configurable: true,
      value: {
        selectMedia: vi.fn().mockResolvedValue(["C:/videos/a.mp4", "C:/videos/a.mp4", "C:/videos/b.mp4"]),
      },
    });
    const { result } = renderHook(() => {
      const [message, setMessage] = useState("");
      const [error, setError] = useState("");
      const draft = usePublishDraft({ l, setMessage, setError });
      return { ...draft, message, error };
    });

    await act(async () => result.current.chooseFiles());
    expect(result.current.items.map((item) => item.file_path)).toEqual(["C:/videos/a.mp4", "C:/videos/b.mp4"]);
    expect(result.current.missingCaptionCount).toBe(2);

    act(() => {
      result.current.updateItem("C:/videos/a.mp4", { caption: "Caption A" });
      result.current.updateItem("C:/videos/b.mp4", { caption: "Caption B" });
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      result.current.setScheduleStartDate(localDateValue(tomorrow));
      result.current.setScheduleTimes("09:00, 19:00");
      result.current.setVideosPerDay(2);
    });
    act(() => result.current.applyAutoSchedule());

    expect(result.current.validItems).toBe(true);
    expect(result.current.items.every((item) => Boolean(item.scheduled_at))).toBe(true);
    expect(result.current.message).toContain("Scheduled 2 videos");
    expect(result.current.error).toBe("");
  });

  it("applies one shared caption to every selected video and keeps an editable batch name", async () => {
    Object.defineProperty(window, "dyna", {
      configurable: true,
      value: {
        selectMedia: vi.fn().mockResolvedValue(["C:/videos/a.mp4", "C:/videos/b.mp4"]),
      },
    });
    const { result } = renderHook(() => {
      const [message, setMessage] = useState("");
      const [error, setError] = useState("");
      const draft = usePublishDraft({ l, setMessage, setError });
      return { ...draft, message, error };
    });

    await act(async () => result.current.chooseFiles());
    act(() => {
      result.current.setBatchName("Lô game tháng 7");
      result.current.applySharedCaption("Một mô tả chung #game");
    });

    expect(result.current.batchName).toBe("Lô game tháng 7");
    expect(result.current.items.map((item) => item.caption)).toEqual([
      "Một mô tả chung #game",
      "Một mô tả chung #game",
    ]);
    expect(result.current.message).toContain("Applied the same caption to 2 videos");
  });
});

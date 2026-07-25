import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/client";
import { usePublishCenter } from "./usePublishCenter";
import { usePublishDraft } from "./usePublishDraft";
import { usePublishQueue } from "./usePublishQueue";
import { usePublishTargets } from "./usePublishTargets";

vi.mock("../../../shared/api/client", () => ({ request: vi.fn() }));
vi.mock("../../../shared/i18n", () => ({
  useI18n: () => ({ l: (_vi: string, en: string) => en }),
}));
vi.mock("./usePublishDraft", () => ({ usePublishDraft: vi.fn() }));
vi.mock("./usePublishQueue", () => ({ usePublishQueue: vi.fn() }));
vi.mock("./usePublishTargets", () => ({ usePublishTargets: vi.fn() }));

const requestMock = vi.mocked(request);
const draftMock = vi.mocked(usePublishDraft);
const queueMock = vi.mocked(usePublishQueue);
const targetsMock = vi.mocked(usePublishTargets);
const setItems = vi.fn();
const refreshJobs = vi.fn().mockResolvedValue(undefined);

describe("usePublishCenter publishing flow", () => {
  beforeEach(() => {
    requestMock.mockReset();
    setItems.mockClear();
    refreshJobs.mockClear();
    draftMock.mockReturnValue({
      items: [{ file_path: "C:/videos/a.mp4", caption: "  Caption A  ", scheduled_at: "" }],
      setItems,
      scheduleStartDate: "2026-07-25",
      setScheduleStartDate: vi.fn(),
      videosPerDay: 2,
      setVideosPerDay: vi.fn(),
      scheduleTimes: "09:00, 19:00",
      setScheduleTimes: vi.fn(),
      missingCaptionCount: 0,
      invalidScheduleCount: 0,
      validItems: true,
      chooseFiles: vi.fn(),
      updateItem: vi.fn(),
      applyAutoSchedule: vi.fn(),
      clearAllSchedules: vi.fn(),
    });
    targetsMock.mockReturnValue({
      profiles: {
        data: { profiles: [] },
        error: "",
        loading: false,
        refresh: vi.fn(),
        setData: vi.fn(),
      },
      targets: { "1": ["tiktok"] },
      checkingReady: false,
      selectedTargets: [{ profile_id: "1", platforms: ["tiktok"] }],
      selectedTargetsReady: true,
      selectedTargetsChecking: false,
      toggleProfile: vi.fn(),
      togglePlatform: vi.fn(),
    });
    queueMock.mockReturnValue({
      jobs: {
        data: { jobs: [], total: 0 },
        error: "",
        loading: false,
        refresh: refreshJobs,
        setData: vi.fn(),
      },
      busyJob: "",
      jobAction: vi.fn(),
      resetQueue: vi.fn(),
    });
  });

  it("creates publishing jobs with normalized captions and refreshes the queue", async () => {
    requestMock.mockResolvedValue({
      job_count: 1,
      file_count: 1,
      scheduled_count: 0,
    });
    const { result } = renderHook(() => usePublishCenter());

    await act(async () => result.current.publish());

    expect(requestMock).toHaveBeenCalledWith("/api/publisher/jobs", {
      method: "POST",
      body: {
        items: [{ file_path: "C:/videos/a.mp4", caption: "Caption A", scheduled_at: "" }],
        targets: [{ profile_id: "1", platforms: ["tiktok"] }],
      },
      timeoutMs: 120_000,
    });
    expect(setItems).toHaveBeenCalledWith([]);
    expect(refreshJobs).toHaveBeenCalledOnce();
    expect(result.current.message).toBe("Created 1 jobs from 1 videos.");
  });
});

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/client";
import { usePublishCenter } from "./usePublishCenter";
import { usePublishDraft } from "./usePublishDraft";
import { useDouyinSelection } from "./useDouyinSelection";
import { usePublishQueue } from "./usePublishQueue";
import { usePublishTargets } from "./usePublishTargets";

vi.mock("../../../shared/api/client", () => ({ request: vi.fn() }));
vi.mock("../../../shared/i18n", () => ({
  useI18n: () => ({ l: (_vi: string, en: string) => en }),
}));
vi.mock("./usePublishDraft", () => ({ usePublishDraft: vi.fn() }));
vi.mock("./useDouyinSelection", () => ({ useDouyinSelection: vi.fn() }));
vi.mock("./usePublishQueue", () => ({ usePublishQueue: vi.fn() }));
vi.mock("./usePublishTargets", () => ({ usePublishTargets: vi.fn() }));

const requestMock = vi.mocked(request);
const draftMock = vi.mocked(usePublishDraft);
const douyinSelectionMock = vi.mocked(useDouyinSelection);
const queueMock = vi.mocked(usePublishQueue);
const targetsMock = vi.mocked(usePublishTargets);
const setItems = vi.fn();
const renewBatchName = vi.fn();
const refreshJobs = vi.fn().mockResolvedValue(undefined);

describe("usePublishCenter publishing flow", () => {
  beforeEach(() => {
    requestMock.mockReset();
    douyinSelectionMock.mockReturnValue({} as ReturnType<typeof useDouyinSelection>);
    setItems.mockClear();
    renewBatchName.mockClear();
    refreshJobs.mockClear();
    draftMock.mockReturnValue({
      items: [{ file_path: "C:/videos/a.mp4", caption: "  Caption A  ", scheduled_at: "" }],
      setItems,
      batchName: "July gaming batch",
      setBatchName: vi.fn(),
      scheduleStartDate: "2026-07-25",
      setScheduleStartDate: vi.fn(),
      videosPerDay: 2,
      setVideosPerDay: vi.fn(),
      scheduleTimes: "09:00, 19:00",
      setScheduleTimes: vi.fn(),
      scheduleMode: "fixed",
      setScheduleMode: vi.fn(),
      randomStartTime: "09:00",
      setRandomStartTime: vi.fn(),
      randomEndTime: "21:00",
      setRandomEndTime: vi.fn(),
      missingCaptionCount: 0,
      invalidScheduleCount: 0,
      validItems: true,
      chooseFiles: vi.fn(),
      updateItem: vi.fn(),
      applySharedCaption: vi.fn(),
      renewBatchName,
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
        batch_name: "July gaming batch",
        items: [{ file_path: "C:/videos/a.mp4", caption: "Caption A", scheduled_at: "" }],
        targets: [{ profile_id: "1", platforms: ["tiktok"] }],
      },
      timeoutMs: 120_000,
    });
    expect(setItems).toHaveBeenCalledWith([]);
    expect(renewBatchName).toHaveBeenCalledOnce();
    expect(refreshJobs).toHaveBeenCalledOnce();
    expect(result.current.message).toBe("Created 1 jobs from 1 videos.");
  });

  it("generates a separate AI caption for every video from its original description", async () => {
    draftMock.mockReturnValue({
      ...draftMock(),
      items: [
        {
          file_path: "douyin://selection/a",
          video_id: "a",
          caption: "Original A",
          original_description: "Original A",
          scheduled_at: "",
          source_type: "douyin",
        },
        {
          file_path: "douyin://selection/b",
          video_id: "b",
          caption: "Original B",
          original_description: "Original B",
          scheduled_at: "",
          source_type: "douyin",
        },
      ],
    });
    requestMock
      .mockResolvedValueOnce({ caption: "AI caption A", model: "dyna-model" })
      .mockResolvedValueOnce({ caption: "AI caption B", model: "dyna-model" });
    const { result } = renderHook(() => usePublishCenter());

    await act(async () => result.current.generateAiCaptions("Rewrite with a playful tone"));

    expect(requestMock).toHaveBeenCalledTimes(2);
    expect(requestMock).toHaveBeenNthCalledWith(1, "/api/assistant/captions/generate", {
      method: "POST",
      body: {
        original_description: "Original A",
        instruction: "Rewrite with a playful tone",
        video_label: "a",
      },
      timeoutMs: 90_000,
    });
    const update = setItems.mock.calls.at(-1)?.[0] as (
      items: Array<Record<string, string>>,
    ) => Array<Record<string, string>>;
    expect(update(draftMock().items as unknown as Array<Record<string, string>>).map((item) => item.caption)).toEqual([
      "AI caption A",
      "AI caption B",
    ]);
    expect(result.current.message).toContain("DynaAI created 2/2 unique captions");
  });
});

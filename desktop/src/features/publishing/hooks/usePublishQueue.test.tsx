import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/client";
import { usePolling } from "../../../shared/hooks/usePolling";
import type { Job } from "../../../shared/types";
import { usePublishQueue } from "./usePublishQueue";

vi.mock("../../../shared/api/client", () => ({ request: vi.fn() }));
vi.mock("../../../shared/hooks/usePolling", () => ({ usePolling: vi.fn() }));

const requestMock = vi.mocked(request);
const pollingMock = vi.mocked(usePolling);
const refresh = vi.fn().mockResolvedValue(undefined);
const job = {
  profile_id: "1",
  video_id: "video-1",
  status: "failed_upload",
} as Job;

describe("usePublishQueue", () => {
  beforeEach(() => {
    requestMock.mockReset();
    refresh.mockClear();
    pollingMock.mockReturnValue({
      data: { jobs: [job], total: 1 },
      error: "",
      loading: false,
      refresh,
      setData: vi.fn(),
    });
  });

  it("retries a failed job and refreshes the queue", async () => {
    requestMock.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => {
      const [, setMessage] = useState("");
      const [, setError] = useState("");
      return usePublishQueue({ l: (_vi, en) => en, setMessage, setError });
    });

    await act(async () => result.current.jobAction("retry", job));

    expect(requestMock).toHaveBeenCalledWith("/api/publisher/jobs/retry", {
      method: "POST",
      body: { profile_id: "1", video_id: "video-1" },
    });
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("resets the queue only after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    requestMock.mockResolvedValue({ reset_count: 1 });
    const { result } = renderHook(() => {
      const [message, setMessage] = useState("");
      const [, setError] = useState("");
      return { ...usePublishQueue({ l: (_vi, en) => en, setMessage, setError }), message };
    });

    await act(async () => result.current.resetQueue());

    expect(requestMock).toHaveBeenCalledWith("/api/publisher/jobs/reset", { method: "POST" });
    expect(result.current.message).toBe("Reset 1 videos from the queue.");
  });
});

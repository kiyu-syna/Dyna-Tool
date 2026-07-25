import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/client";
import { usePolling } from "../../shared/hooks/usePolling";
import type { ProfileSummary, RuntimeSnapshot } from "../../shared/types";
import TrackingPage from "./TrackingPage";

vi.mock("../../shared/api/client", () => ({ request: vi.fn() }));
vi.mock("../../shared/hooks/usePolling", () => ({ usePolling: vi.fn() }));
vi.mock("../../shared/i18n", () => ({
  useI18n: () => ({ l: (_vi: string, en: string) => en }),
}));
vi.mock("../../shared/components/VideoQueueSection", () => ({
  default: () => <div data-testid="video-queue" />,
}));

const requestMock = vi.mocked(request);
const pollingMock = vi.mocked(usePolling);
const profileRefresh = vi.fn().mockResolvedValue(undefined);
const runtimeRefresh = vi.fn().mockResolvedValue(undefined);
const profile = {
  id: "1",
  name: "Profile 1",
  enabled: true,
  source_count: 1,
  enabled_source_count: 1,
  source_platforms: { douyin: 1, tiktok: 0 },
  check_interval_minutes: 30,
  platforms: { tiktok: true, youtube: false, facebook: false },
  running: false,
  current_video: "",
  queue_count: 0,
  error_count: 0,
} satisfies ProfileSummary;
const runtime = {
  profiles: {},
  active_profile_ids: [],
} satisfies RuntimeSnapshot;

describe("TrackingPage runtime flow", () => {
  beforeEach(() => {
    requestMock.mockReset();
    profileRefresh.mockClear();
    runtimeRefresh.mockClear();
    pollingMock.mockImplementation((path) => {
      if (path === "/api/profiles") {
        return {
          data: { profiles: [profile] },
          error: "",
          loading: false,
          refresh: profileRefresh,
          setData: vi.fn(),
        } as never;
      }
      if (path === "/api/runtime") {
        return {
          data: runtime,
          error: "",
          loading: false,
          refresh: runtimeRefresh,
          setData: vi.fn(),
        } as never;
      }
      return {
        data: { profiles: {} },
        error: "",
        loading: false,
        refresh: vi.fn().mockResolvedValue(undefined),
        setData: vi.fn(),
      } as never;
    });
  });

  it("starts one Profile and refreshes both runtime and Profile summaries", async () => {
    requestMock.mockResolvedValue({ ok: true });
    render(<TrackingPage />);

    fireEvent.click(screen.getByRole("button", { name: "Start" }));

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith("/api/runtime/profiles/1/start", { method: "POST" });
    });
    expect(runtimeRefresh).toHaveBeenCalledOnce();
    expect(profileRefresh).toHaveBeenCalledOnce();
  });
});

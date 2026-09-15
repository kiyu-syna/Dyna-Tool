import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../shared/api/client";
import { usePolling } from "../../shared/hooks/usePolling";
import type { ProfileSummary, RuntimeSnapshot, TestUploadState } from "../../shared/types";
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

  it("shows the final result and each failed platform", () => {
    const failedTest = {
      profile_id: "1",
      status: "completed_with_errors",
      active: false,
      message: "Hoàn tất test: 0 thành công, 1 thất bại.",
      video_id: "video-1",
      platforms: ["tiktok"],
      results: {
        tiktok: {
          ok: false,
          message: "TikTok đã nhận thao tác Đăng nhưng không xác nhận trong 45 giây.",
        },
      },
      last_error: "",
      started_at: "2026-09-14T17:22:00",
      updated_at: "2026-09-14T17:23:00",
    } satisfies TestUploadState;
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
        data: { profiles: { "1": failedTest } },
        error: "",
        loading: false,
        refresh: vi.fn().mockResolvedValue(undefined),
        setData: vi.fn(),
      } as never;
    });

    render(<TrackingPage />);

    expect(screen.getByRole("status")).toHaveTextContent("Test-publish result");
    expect(screen.getByRole("status")).toHaveTextContent("TikTok đã nhận thao tác Đăng");
  });

  it("opens a screenshot from automatic error history", async () => {
    requestMock.mockResolvedValue({ data_url: "data:image/png;base64,cG5n" });
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
      if (path === "/api/diagnostics/history?limit=12") {
        return {
          data: {
            items: [
              {
                event_id: "event-1",
                occurred_at: "2026-09-14T17:23:00",
                profile_id: "1",
                video_id: "video-1",
                platform: "tiktok",
                url: "https://www.tiktok.com/tiktokstudio/upload",
                error: "TikTok không xác nhận đăng trong 45 giây.",
                last_response: {},
                screenshot_available: true,
                screenshot_error: "",
              },
            ],
          },
          error: "",
          loading: false,
          refresh: vi.fn(),
          setData: vi.fn(),
        } as never;
      }
      return {
        data: { profiles: {} },
        error: "",
        loading: false,
        refresh: vi.fn(),
        setData: vi.fn(),
      } as never;
    });

    render(<TrackingPage />);
    fireEvent.click(screen.getByRole("button", { name: /TikTok không xác nhận đăng/i }));

    await waitFor(() => {
      expect(requestMock).toHaveBeenCalledWith("/api/diagnostics/history/event-1/screenshot");
    });
    expect(await screen.findByAltText("Screenshot at failure")).toHaveAttribute("src", "data:image/png;base64,cG5n");
  });
});

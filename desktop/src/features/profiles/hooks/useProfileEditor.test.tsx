import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/client";
import { usePolling } from "../../../shared/hooks/usePolling";
import type { ProfileConfig, ProfileSummary } from "../../../shared/types";
import { useProfileEditor } from "./useProfileEditor";

vi.mock("../../../shared/api/client", () => ({ request: vi.fn() }));
vi.mock("../../../shared/hooks/usePolling", () => ({ usePolling: vi.fn() }));

const requestMock = vi.mocked(request);
const pollingMock = vi.mocked(usePolling);
const refresh = vi.fn().mockResolvedValue(undefined);
const summary = {
  id: "1",
  name: "Profile 1",
  enabled: true,
  source_count: 1,
  enabled_source_count: 1,
  check_interval_minutes: 30,
  platforms: { tiktok: true, youtube: false, facebook: false },
  running: false,
  current_video: "",
  queue_count: 0,
  error_count: 0,
} satisfies ProfileSummary;

describe("useProfileEditor", () => {
  beforeEach(() => {
    requestMock.mockReset();
    refresh.mockClear();
    pollingMock.mockReturnValue({
      data: { profiles: [summary] },
      error: "",
      loading: false,
      refresh,
      setData: vi.fn(),
    });
  });

  it("loads, edits, and saves while preserving browser settings owned by the browser feature", async () => {
    const initial = {
      id: "1",
      name: "Profile 1",
      enabled: true,
      browser: { provider: "local_chromium", headless: false },
      tiktok: { enabled: true },
    } as ProfileConfig;
    const latest = {
      ...initial,
      browser: { provider: "local_chromium", headless: true, user_data_dir: "C:/profiles/1" },
    } as ProfileConfig;
    let getCount = 0;
    requestMock.mockImplementation(async (_path, options) => {
      if (options?.method === "PUT") {
        return { profile: (options.body as { profile: ProfileConfig }).profile } as never;
      }
      getCount += 1;
      return { profile: getCount === 1 ? initial : latest } as never;
    });

    const { result } = renderHook(() => useProfileEditor((_vi, en) => en));
    await waitFor(() => expect(result.current.profile?.id).toBe("1"));

    act(() => result.current.update({ name: "Renamed Profile" }));
    await act(async () => result.current.save());

    const putCall = requestMock.mock.calls.find(([, options]) => options?.method === "PUT");
    expect((putCall?.[1]?.body as { profile: ProfileConfig }).profile).toMatchObject({
      name: "Renamed Profile",
      browser: latest.browser,
    });
    expect(refresh).toHaveBeenCalledOnce();
    expect(result.current.message).toBe("Profile settings saved");
  });
});

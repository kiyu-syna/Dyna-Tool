import { act, renderHook, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../../shared/api/client";
import type { BrowserProfileSetupState, ProfileConfig } from "../../../shared/types";
import { useBrowserSessions } from "./useBrowserSessions";

vi.mock("../../../shared/api/client", () => ({ request: vi.fn() }));

const requestMock = vi.mocked(request);
const l = (_vi: string, en: string) => en;

function setupState(overrides: Partial<BrowserProfileSetupState>): BrowserProfileSetupState {
  return {
    profile_id: "1",
    status: "running",
    active: true,
    message: "",
    user_data_dir: "C:/profiles/1",
    executable_path: "C:/Chrome/chrome.exe",
    profile_directory: "Default",
    sessions: {},
    last_error: "",
    started_at: "2026-07-24T10:00:00Z",
    updated_at: "2026-07-24T10:00:00Z",
    ...overrides,
  };
}

describe("useBrowserSessions", () => {
  beforeEach(() => requestMock.mockReset());

  it("surfaces an unexpected browser close and expands the affected Profile", async () => {
    const state = setupState({
      status: "error",
      active: false,
      last_error: "Chrome closed unexpectedly",
    });
    const { result } = renderHook(() => {
      const [expandedIds, setExpandedIds] = useState<string[]>([]);
      const [message, setMessage] = useState("");
      const [messageError, setMessageError] = useState(false);
      useBrowserSessions({
        sessions: { "1": state },
        refreshSessions: vi.fn(),
        reloadProfile: vi.fn(),
        updateProfile: vi.fn(),
        setBusy: vi.fn(),
        setExpandedIds,
        setMessage,
        setMessageError,
        l,
      });
      return { expandedIds, message, messageError };
    });

    await waitFor(() => expect(result.current.expandedIds).toEqual(["1"]));
    expect(result.current.message).toBe("Chrome closed unexpectedly");
    expect(result.current.messageError).toBe(true);
  });

  it("opens a Profile with a bounded request and refreshes session state", async () => {
    const refreshSessions = vi.fn().mockResolvedValue(undefined);
    requestMock.mockResolvedValue({ ok: true });
    const profile = { id: "1", name: "Profile 1", browser: {} } as ProfileConfig;
    const { result } = renderHook(() => {
      const [, setExpandedIds] = useState<string[]>([]);
      const [, setMessage] = useState("");
      const [, setMessageError] = useState(false);
      return useBrowserSessions({
        sessions: {},
        refreshSessions,
        reloadProfile: vi.fn(),
        updateProfile: vi.fn(),
        setBusy: vi.fn(),
        setExpandedIds,
        setMessage,
        setMessageError,
        l,
      });
    });

    await act(async () => result.current.openLocalProfile(profile));

    expect(requestMock).toHaveBeenCalledWith("/api/browser-profiles/1/open", {
      method: "POST",
      timeoutMs: 120_000,
    });
    expect(refreshSessions).toHaveBeenCalledOnce();
  });
});

import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import { request } from "../../../shared/api/client";
import type { BrowserProfileSetupState, ProfileConfig } from "../../../shared/types";

type Localize = (vi: string, en: string, zh: string) => string;

type BrowserSessionsOptions = {
  sessions: Record<string, BrowserProfileSetupState>;
  refreshSessions(): Promise<void>;
  reloadProfile(profileId: string): Promise<void>;
  updateProfile(profileId: string, transform: (profile: ProfileConfig) => ProfileConfig): void;
  setBusy(profileId: string, busy: boolean): void;
  setExpandedIds: Dispatch<SetStateAction<string[]>>;
  setMessage: Dispatch<SetStateAction<string>>;
  setMessageError: Dispatch<SetStateAction<boolean>>;
  l: Localize;
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useBrowserSessions({
  sessions,
  refreshSessions,
  reloadProfile,
  updateProfile,
  setBusy,
  setExpandedIds,
  setMessage,
  setMessageError,
  l,
}: BrowserSessionsOptions) {
  const observedSetups = useRef<Record<string, string>>({});

  useEffect(() => {
    for (const [profileId, setup] of Object.entries(sessions)) {
      const eventKey = `${setup.status}:${setup.completed_at || setup.updated_at}`;
      if (observedSetups.current[profileId] === eventKey) continue;
      observedSetups.current[profileId] = eventKey;
      if (setup.status === "completed") void reloadProfile(profileId);
      if (setup.status === "error") {
        setExpandedIds((current) => (current.includes(profileId) ? current : [...current, profileId]));
        setMessage(
          setup.last_error ||
            setup.message ||
            l(
              `Trình duyệt của hồ sơ ${profileId} đã đóng bất ngờ.`,
              `The browser for Profile ${profileId} closed unexpectedly.`,
              `配置文件 ${profileId} 的浏览器意外关闭。`,
            ),
        );
        setMessageError(true);
      }
    }
  }, [l, reloadProfile, sessions, setExpandedIds, setMessage, setMessageError]);

  async function runSessionAction(profileId: string, action: () => Promise<void>) {
    setBusy(profileId, true);
    setMessage("");
    try {
      await action();
      setMessageError(false);
      await refreshSessions();
    } catch (error) {
      setExpandedIds((current) => (current.includes(profileId) ? current : [...current, profileId]));
      setMessage(errorText(error));
      setMessageError(true);
    } finally {
      setBusy(profileId, false);
    }
  }

  async function startLocalProfile(profile: ProfileConfig) {
    await runSessionAction(profile.id, async () => {
      const result = await request<{ state: BrowserProfileSetupState }>(
        `/api/browser-profiles/${profile.id}/initialize`,
        { method: "POST", body: { executable_path: "" }, timeoutMs: 120_000 },
      );
      updateProfile(profile.id, (current) => ({
        ...current,
        browser: {
          ...(current.browser || {}),
          provider: "local_chromium",
          user_data_dir: result.state.user_data_dir,
          executable_path: result.state.executable_path,
        },
      }));
      setMessage(
        l(
          `Đã mở Chrome chính thức cho hồ sơ ${profile.id}.`,
          `Official Chrome opened for Profile ${profile.id}.`,
          `已为配置文件 ${profile.id} 打开官方 Chrome。`,
        ),
      );
    });
  }

  async function openLocalProfile(profile: ProfileConfig) {
    await runSessionAction(profile.id, async () => {
      await request(`/api/browser-profiles/${profile.id}/open`, {
        method: "POST",
        timeoutMs: 120_000,
      });
      setMessage(
        l(
          `Đã mở đăng nhập/CAPTCHA cho hồ sơ ${profile.id}.`,
          `Sign-in/CAPTCHA window opened for Profile ${profile.id}.`,
          `已为配置文件 ${profile.id} 打开登录/验证码窗口。`,
        ),
      );
    });
  }

  async function finishLocalProfile(profileId: string) {
    await runSessionAction(profileId, async () => {
      await request(`/api/browser-profiles/${profileId}/finish`, {
        method: "POST",
        timeoutMs: 120_000,
      });
      setMessage(
        l(
          `Đang lưu phiên đăng nhập và đóng Chromium của hồ sơ ${profileId}.`,
          `Saving the session and closing Chromium for Profile ${profileId}.`,
          `正在保存配置文件 ${profileId} 的会话并关闭 Chromium。`,
        ),
      );
    });
  }

  return { startLocalProfile, openLocalProfile, finishLocalProfile };
}

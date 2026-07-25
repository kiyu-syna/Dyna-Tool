import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { request } from "../../../shared/api/client";
import type { ProfileConfig, ProfileSummary } from "../../../shared/types";
import { validateProxyServer } from "../utils/proxy";
import { hasCompleteLocalBrowser, mergeBrowserSettings, providerOf } from "../utils/profile";

type Localize = (vi: string, en: string, zh: string) => string;

type BrowserProfilesOptions = {
  summaries: ProfileSummary[];
  l: Localize;
  setBusy(profileId: string, busy: boolean): void;
  setExpandedIds: Dispatch<SetStateAction<string[]>>;
  setMessage: Dispatch<SetStateAction<string>>;
  setMessageError: Dispatch<SetStateAction<boolean>>;
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useBrowserProfiles({
  summaries,
  l,
  setBusy,
  setExpandedIds,
  setMessage,
  setMessageError,
}: BrowserProfilesOptions) {
  const [profiles, setProfiles] = useState<Record<string, ProfileConfig>>({});
  const [loadingProfiles, setLoadingProfiles] = useState(true);
  const loadedSignature = useRef("");
  const profileSignature = summaries.map((item) => item.id).join("|");

  const profileList = useMemo(
    () =>
      summaries
        .map((summary) => ({ summary, profile: profiles[summary.id] }))
        .filter((row): row is { summary: ProfileSummary; profile: ProfileConfig } => Boolean(row.profile)),
    [profiles, summaries],
  );

  useEffect(() => {
    if (!profileSignature || loadedSignature.current === profileSignature) {
      if (!profileSignature) setLoadingProfiles(false);
      return;
    }
    loadedSignature.current = profileSignature;
    let cancelled = false;
    setLoadingProfiles(true);
    void Promise.all(
      summaries.map(async (summary) => {
        const result = await request<{ profile: ProfileConfig }>(`/api/profiles/${summary.id}`);
        return result.profile;
      }),
    )
      .then((rows) => {
        if (cancelled) return;
        setProfiles(Object.fromEntries(rows.map((profile) => [profile.id, profile])));
        setMessageError(false);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        loadedSignature.current = "";
        setMessage(errorText(error));
        setMessageError(true);
      })
      .finally(() => {
        if (!cancelled) setLoadingProfiles(false);
      });
    return () => {
      cancelled = true;
    };
  }, [profileSignature, summaries, setMessage, setMessageError]);

  async function reloadProfile(profileId: string) {
    try {
      const result = await request<{ profile: ProfileConfig }>(`/api/profiles/${profileId}`);
      setProfiles((current) => ({ ...current, [profileId]: result.profile }));
    } catch (error) {
      setMessage(errorText(error));
      setMessageError(true);
    }
  }

  async function persistProfile(profile: ProfileConfig) {
    setBusy(profile.id, true);
    try {
      const latest = await request<{ profile: ProfileConfig }>(`/api/profiles/${profile.id}`);
      const merged = mergeBrowserSettings(latest.profile, profile);
      const result = await request<{ profile: ProfileConfig }>(`/api/profiles/${profile.id}`, {
        method: "PUT",
        body: { profile: merged },
      });
      setProfiles((current) => ({ ...current, [profile.id]: result.profile }));
      setMessage(
        l(
          `Đã lưu trình duyệt cho hồ sơ ${profile.id}.`,
          `Browser settings saved for Profile ${profile.id}.`,
          `已保存配置文件 ${profile.id} 的浏览器设置。`,
        ),
      );
      setMessageError(false);
    } catch (error) {
      setMessage(errorText(error));
      setMessageError(true);
      throw error;
    } finally {
      setBusy(profile.id, false);
    }
  }

  async function saveDetails(profileId: string) {
    const profile = profiles[profileId];
    if (!profile) return;
    if (providerOf(profile) === "local_chromium" && !hasCompleteLocalBrowser(profile)) {
      setExpandedIds((current) => (current.includes(profileId) ? current : [...current, profileId]));
      setMessage(
        l(
          "Để nhập hồ sơ có sẵn, hãy điền cả thư mục dữ liệu người dùng và file chạy Chromium. Nếu tạo mới, chỉ cần bấm Tạo hồ sơ Local.",
          "To import an existing profile, enter both the User Data Directory and Chromium executable. For a new profile, select Create Local Profile.",
          "要导入现有配置文件，请同时填写用户数据目录和 Chromium 可执行文件。若要新建，只需点击“创建本地配置文件”。",
        ),
      );
      setMessageError(true);
      return;
    }
    if (profile.browser?.proxy?.enabled) {
      const validation = validateProxyServer(profile.browser.proxy.server);
      if (!validation.valid) {
        setExpandedIds((current) => (current.includes(profileId) ? current : [...current, profileId]));
        setMessage(validation.message);
        setMessageError(true);
        return;
      }
    }
    try {
      await persistProfile(profile);
    } catch {
      // persistProfile already reports the actionable error.
    }
  }

  return {
    profiles,
    setProfiles,
    profileList,
    loadingProfiles,
    reloadProfile,
    saveDetails,
  };
}

import { useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { request } from "../../../shared/api/client";
import type { PlatformKey, ProfileConfig, ProfileSummary, PublisherReadiness } from "../../../shared/types";
import { PUBLISH_PLATFORMS } from "../constants";

type Localize = (vi: string, en: string, zh: string) => string;

type ProfileRow = { summary: ProfileSummary; profile: ProfileConfig };

type PublisherLoginStatusOptions = {
  profileList: ProfileRow[];
  readiness: PublisherReadiness | null;
  refreshReadiness(): Promise<void>;
  setMessage: Dispatch<SetStateAction<string>>;
  setMessageError: Dispatch<SetStateAction<boolean>>;
  l: Localize;
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function usePublisherLoginStatus({
  profileList,
  readiness,
  refreshReadiness,
  setMessage,
  setMessageError,
  l,
}: PublisherLoginStatusOptions) {
  const [checkingLoginIds, setCheckingLoginIds] = useState<string[]>([]);
  const [checkingAllLogins, setCheckingAllLogins] = useState(false);
  const readinessByKey = useMemo(
    () => new Map((readiness?.checks || []).map((check) => [`${check.profile_id}:${check.platform}`, check])),
    [readiness],
  );
  const enabledLoginTargets = profileList.flatMap(({ profile }) =>
    PUBLISH_PLATFORMS.filter((platform) => profile[platform]?.enabled === true).map((platform) => ({
      profileId: profile.id,
      platform,
    })),
  );
  const readyLoginCount = enabledLoginTargets.filter(
    ({ profileId, platform }) => readinessByKey.get(`${profileId}:${platform}`)?.ready,
  ).length;
  const allLoginCheckInProgress = checkingAllLogins || readiness?.checking === true;

  function setCheckingLogin(profileId: string, checking: boolean) {
    setCheckingLoginIds((current) =>
      checking
        ? current.includes(profileId)
          ? current
          : [...current, profileId]
        : current.filter((id) => id !== profileId),
    );
  }

  async function refreshTargets(targets: Array<{ profile_id: string; platforms: PlatformKey[] }>) {
    await request<PublisherReadiness>("/api/publisher/readiness/refresh", {
      method: "POST",
      body: { targets, force: true },
      timeoutMs: 120_000,
    });
    await refreshReadiness();
  }

  async function refreshLoginStatus(profile: ProfileConfig) {
    const platforms = PUBLISH_PLATFORMS.filter((platform) => profile[platform]?.enabled === true);
    if (!platforms.length) {
      setMessage(
        l(
          "Hồ sơ này chưa bật nền tảng đăng nào để kiểm tra đăng nhập.",
          "This Profile has no publishing platform enabled to check.",
          "此配置文件尚未启用可检查登录状态的发布平台。",
        ),
      );
      setMessageError(true);
      return;
    }
    setCheckingLogin(profile.id, true);
    setMessage("");
    try {
      await refreshTargets([{ profile_id: profile.id, platforms }]);
      setMessageError(false);
    } catch (error) {
      setMessage(errorText(error));
      setMessageError(true);
    } finally {
      setCheckingLogin(profile.id, false);
    }
  }

  async function refreshAllLoginStatuses() {
    const targets = profileList.flatMap(({ profile }) => {
      const platforms = PUBLISH_PLATFORMS.filter((platform) => profile[platform]?.enabled === true);
      return platforms.length ? [{ profile_id: profile.id, platforms }] : [];
    });
    if (!targets.length) {
      setMessage(
        l(
          "Chưa có nền tảng đăng nào được bật để kiểm tra.",
          "No publishing platform is enabled to check.",
          "尚未启用可检查的发布平台。",
        ),
      );
      setMessageError(true);
      return;
    }
    setCheckingAllLogins(true);
    setMessage("");
    try {
      await refreshTargets(targets);
      setMessageError(false);
    } catch (error) {
      setMessage(errorText(error));
      setMessageError(true);
    } finally {
      setCheckingAllLogins(false);
    }
  }

  return {
    readinessByKey,
    enabledLoginTargets,
    readyLoginCount,
    allLoginCheckInProgress,
    checkingLoginIds,
    refreshLoginStatus,
    refreshAllLoginStatuses,
  };
}

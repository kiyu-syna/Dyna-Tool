import { useState, type Dispatch, type SetStateAction } from "react";
import type { PlatformKey, ProfileConfig } from "../../../shared/types";
import type { BrowserRunMode as BrowserRunModeValue, ProxyEndpoint, ProxyScheme } from "../types";
import { parseQuickProxy, proxyEndpointOf, proxyServerOf } from "../utils/proxy";

type Localize = (vi: string, en: string, zh: string) => string;

type BrowserProfileSettingsOptions = {
  profiles: Record<string, ProfileConfig>;
  setProfiles: Dispatch<SetStateAction<Record<string, ProfileConfig>>>;
  l: Localize;
  setMessage: Dispatch<SetStateAction<string>>;
  setMessageError: Dispatch<SetStateAction<boolean>>;
};

export function useBrowserProfileSettings({
  profiles,
  setProfiles,
  l,
  setMessage,
  setMessageError,
}: BrowserProfileSettingsOptions) {
  const [proxyQuickValues, setProxyQuickValues] = useState<Record<string, string>>({});

  function updateProfile(profileId: string, transform: (profile: ProfileConfig) => ProfileConfig) {
    setProfiles((current) => {
      const profile = current[profileId];
      return profile ? { ...current, [profileId]: transform(profile) } : current;
    });
  }

  function updateBrowser(profileId: string, field: string, value: unknown) {
    updateProfile(profileId, (profile) => ({
      ...profile,
      browser: { ...(profile.browser || {}), [field]: value },
    }));
  }

  function setBrowserRunMode(profileId: string, mode: BrowserRunModeValue) {
    updateProfile(profileId, (profile) => ({
      ...profile,
      browser: {
        ...(profile.browser || {}),
        headless: mode === "headless",
        background: false,
      },
    }));
  }

  function updateProxy(profileId: string, field: string, value: unknown) {
    updateProfile(profileId, (profile) => {
      const proxy = { ...(profile.browser?.proxy || {}), [field]: value };
      if (field === "password") proxy.password_set = Boolean(value);
      return {
        ...profile,
        browser: { ...(profile.browser || {}), proxy },
      };
    });
  }

  function updateProxyEndpoint(profileId: string, changes: Partial<ProxyEndpoint>) {
    updateProfile(profileId, (profile) => {
      const current = proxyEndpointOf(profile.browser?.proxy?.server);
      const proxy = {
        ...(profile.browser?.proxy || {}),
        enabled: true,
        server: proxyServerOf({ ...current, ...changes }),
      };
      return { ...profile, browser: { ...(profile.browser || {}), proxy } };
    });
  }

  function selectProxyType(profileId: string, value: string) {
    if (value === "disabled") {
      updateProxy(profileId, "enabled", false);
      return;
    }
    updateProxyEndpoint(profileId, { scheme: value as ProxyScheme });
  }

  function applyQuickProxy(profileId: string) {
    const raw = proxyQuickValues[profileId] || "";
    if (!raw.trim()) return;
    const profile = profiles[profileId];
    if (!profile) return;
    const parsed = parseQuickProxy(raw, proxyEndpointOf(profile.browser?.proxy?.server).scheme);
    const port = Number(parsed?.endpoint.port || 0);
    if (!parsed?.endpoint.host || !Number.isInteger(port) || port < 1 || port > 65535) {
      setMessage(
        l(
          "Proxy không hợp lệ. Dùng định dạng host:port hoặc host:port:username:password.",
          "Invalid proxy. Use host:port or host:port:username:password.",
          "代理格式无效。请使用 host:port 或 host:port:username:password。",
        ),
      );
      setMessageError(true);
      return;
    }
    updateProfile(profileId, (current) => {
      const password = parsed.password;
      const proxy = {
        ...(current.browser?.proxy || {}),
        enabled: true,
        server: proxyServerOf(parsed.endpoint),
        username: parsed.username,
        password,
        password_set: Boolean(password),
      };
      return { ...current, browser: { ...(current.browser || {}), proxy } };
    });
    setMessageError(false);
  }

  function updateGemLoginId(profileId: string, platform: PlatformKey | "douyin", value: string) {
    updateProfile(profileId, (profile) => ({
      ...profile,
      [platform]: { ...(profile[platform] || {}), gemlogin_profile_id: value },
    }));
  }

  function applyOneGemLoginId(profileId: string) {
    const profile = profiles[profileId];
    if (!profile) return;
    const value = String(profile.douyin?.gemlogin_profile_id || profile.id);
    updateProfile(profileId, (current) => ({
      ...current,
      douyin: { ...(current.douyin || {}), gemlogin_profile_id: value },
      tiktok: { ...(current.tiktok || {}), gemlogin_profile_id: value },
      youtube: { ...(current.youtube || {}), gemlogin_profile_id: value },
      facebook: { ...(current.facebook || {}), gemlogin_profile_id: value },
    }));
  }

  return {
    proxyQuickValues,
    setProxyQuickValues,
    updateProfile,
    updateBrowser,
    setBrowserRunMode,
    updateProxy,
    updateProxyEndpoint,
    selectProxyType,
    applyQuickProxy,
    updateGemLoginId,
    applyOneGemLoginId,
  };
}

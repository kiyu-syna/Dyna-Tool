import { useEffect, useState } from "react";
import { request } from "../../../shared/api/client";
import { usePolling } from "../../../shared/hooks/usePolling";
import type { PlatformKey, ProfileConfig, ProfileSummary } from "../../../shared/types";
import { profilePlatforms } from "../constants";

type Localize = (vi: string, en: string, zh: string) => string;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useProfileEditor(l: Localize) {
  const summaries = usePolling<{ profiles: ProfileSummary[] }>("/api/profiles", 10_000);
  const [selectedId, setSelectedId] = useState("");
  const [profile, setProfile] = useState<ProfileConfig | null>(null);
  const [savedProfile, setSavedProfile] = useState<ProfileConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [messageError, setMessageError] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createId, setCreateId] = useState("");
  const [createName, setCreateName] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);

  useEffect(() => {
    if (!selectedId && summaries.data?.profiles.length) {
      setSelectedId(summaries.data.profiles[0].id);
    }
  }, [selectedId, summaries.data]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setProfile(null);
    setMessage("");
    void request<{ profile: ProfileConfig }>(`/api/profiles/${selectedId}`)
      .then((result) => {
        if (cancelled) return;
        setProfile(result.profile);
        setSavedProfile(result.profile);
        setMessageError(false);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setMessage(errorText(error));
        setMessageError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  function update(next: Partial<ProfileConfig>) {
    setMessage("");
    setProfile((current) => (current ? { ...current, ...next } : current));
  }

  function updatePlatform(key: PlatformKey, field: string, value: unknown) {
    setMessage("");
    setProfile((current) =>
      current
        ? {
            ...current,
            [key]: { ...(current[key] || {}), [field]: value },
          }
        : current,
    );
  }

  async function save() {
    if (!profile) return;
    setSaving(true);
    setMessage("");
    try {
      const latest = await request<{ profile: ProfileConfig }>(`/api/profiles/${profile.id}`);
      const merged: ProfileConfig = {
        ...profile,
        browser: latest.profile.browser,
        douyin: {
          ...(profile.douyin || {}),
          gemlogin_profile_id: latest.profile.douyin?.gemlogin_profile_id || profile.id,
        },
        tiktok: {
          ...(profile.tiktok || {}),
          gemlogin_profile_id: latest.profile.tiktok?.gemlogin_profile_id || profile.id,
        },
        youtube: {
          ...(profile.youtube || {}),
          gemlogin_profile_id: latest.profile.youtube?.gemlogin_profile_id || profile.id,
        },
        facebook: {
          ...(profile.facebook || {}),
          gemlogin_profile_id: latest.profile.facebook?.gemlogin_profile_id || profile.id,
        },
      };
      const result = await request<{ profile: ProfileConfig }>(`/api/profiles/${profile.id}`, {
        method: "PUT",
        body: { profile: merged },
      });
      setProfile(result.profile);
      setSavedProfile(result.profile);
      setMessage(l("Đã lưu cấu hình hồ sơ", "Profile settings saved", "配置文件设置已保存"));
      setMessageError(false);
      await summaries.refresh();
    } catch (error) {
      setMessage(errorText(error));
      setMessageError(true);
    } finally {
      setSaving(false);
    }
  }

  async function createProfile() {
    setSaving(true);
    setMessage("");
    try {
      const result = await request<{ profile: ProfileConfig }>("/api/profiles", {
        method: "POST",
        body: { id: createId, name: createName },
      });
      await summaries.refresh();
      setSelectedId(result.profile.id);
      setCreateOpen(false);
      setCreateId("");
      setCreateName("");
      setMessageError(false);
    } catch (error) {
      setMessage(errorText(error));
      setMessageError(true);
    } finally {
      setSaving(false);
    }
  }

  async function deleteProfile() {
    if (!profile) return;
    setSaving(true);
    setMessage("");
    try {
      await request(`/api/profiles/${encodeURIComponent(profile.id)}`, { method: "DELETE" });
      const nextId = summaries.data?.profiles.find((item) => item.id !== profile.id)?.id || "";
      setDeleteOpen(false);
      setProfile(null);
      setSavedProfile(null);
      setSelectedId(nextId);
      setMessageError(false);
      await summaries.refresh();
    } catch (error) {
      setMessage(errorText(error));
      setMessageError(true);
      setDeleteOpen(false);
    } finally {
      setSaving(false);
    }
  }

  const hasChanges = Boolean(profile && savedProfile && JSON.stringify(profile) !== JSON.stringify(savedProfile));
  const enabledPlatformCount = profile ? profilePlatforms.filter(([key]) => profile[key]?.enabled === true).length : 0;

  function selectProfile(profileId: string) {
    if (profileId === selectedId) return;
    if (
      hasChanges &&
      !window.confirm(
        l(
          "Bạn có thay đổi chưa lưu. Chuyển hồ sơ và bỏ các thay đổi này?",
          "You have unsaved changes. Switch Profiles and discard them?",
          "您有未保存的更改。切换配置文件并放弃更改吗？",
        ),
      )
    )
      return;
    setSelectedId(profileId);
  }

  return {
    summaries,
    selectedId,
    profile,
    saving,
    message,
    messageError,
    createOpen,
    setCreateOpen,
    createId,
    setCreateId,
    createName,
    setCreateName,
    deleteOpen,
    setDeleteOpen,
    hasChanges,
    enabledPlatformCount,
    update,
    updatePlatform,
    save,
    createProfile,
    deleteProfile,
    selectProfile,
  };
}

import { useMemo, useState } from "react";
import { request } from "../../../shared/api/client";
import type { ProfileConfig } from "../../../shared/types";
import { normalizeTikTokUsername, sourceIdentity, type TrackingSource } from "../utils";

type TrackingSourcesOptions = {
  profile: ProfileConfig | null;
  update(next: Partial<ProfileConfig>): void;
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useTrackingSources({ profile, update }: TrackingSourcesOptions) {
  const [bulkSources, setBulkSources] = useState("");
  const [testingSource, setTestingSource] = useState(-1);
  const [sourceTestMessage, setSourceTestMessage] = useState("");
  const [sourceTestError, setSourceTestError] = useState(false);
  const sources: NonNullable<ProfileConfig["tracking_sources"]> = useMemo(
    () => profile?.tracking_sources || [],
    [profile],
  );

  function updateSource(index: number, field: string, value: unknown) {
    if (!profile) return;
    const next = sources.map((source, sourceIndex) => (sourceIndex === index ? { ...source, [field]: value } : source));
    update({ tracking_sources: next });
  }

  function updateTikTokUsername(index: number, value: string) {
    if (!profile) return;
    const next = sources.map((source, sourceIndex) =>
      sourceIndex === index
        ? {
            ...source,
            unique_id: value,
            profile_url: "",
            sec_uid: "",
          }
        : source,
    );
    update({ tracking_sources: next });
  }

  function addSource(platform: "douyin" | "tiktok") {
    if (!profile) return;
    update({
      tracking_sources: [
        ...sources,
        {
          platform,
          display_name: "",
          profile_url: "",
          sec_uid: "",
          unique_id: "",
          enabled: true,
          check_interval_minutes: profile.check_interval_minutes || 30,
        },
      ],
    });
  }

  function removeSource(index: number) {
    if (!profile) return;
    update({ tracking_sources: sources.filter((_, sourceIndex) => sourceIndex !== index) });
  }

  function addBulkSources() {
    if (!profile) return;
    const existing = new Set(sources.map(sourceIdentity).filter(Boolean));
    const additions: TrackingSource[] = [];

    for (const rawLine of bulkSources.split(/[\r\n,]+/)) {
      const parts = rawLine
        .split("|")
        .map((part) => part.trim())
        .filter(Boolean);
      if (!parts.length) continue;
      const explicitPlatform = parts[0].toLocaleLowerCase();
      const hasExplicitPlatform = explicitPlatform === "tiktok" || explicitPlatform === "douyin";
      const identifier = parts.at(-1) || "";
      const platform: "tiktok" | "douyin" =
        explicitPlatform === "tiktok"
          ? "tiktok"
          : explicitPlatform === "douyin"
            ? "douyin"
            : identifier.startsWith("@") || /tiktok\.com\/@/i.test(identifier)
              ? "tiktok"
              : "douyin";
      const displayName = hasExplicitPlatform ? parts.slice(1, -1).join(" | ") : parts.slice(0, -1).join(" | ");
      const source: TrackingSource =
        platform === "tiktok"
          ? {
              platform,
              unique_id: normalizeTikTokUsername(identifier),
              display_name: displayName,
              enabled: true,
              check_interval_minutes: profile.check_interval_minutes || 30,
            }
          : {
              platform,
              sec_uid: identifier,
              display_name: displayName,
              enabled: true,
              check_interval_minutes: profile.check_interval_minutes || 30,
            };
      const identity = sourceIdentity(source);
      if (!identity || existing.has(identity)) continue;
      existing.add(identity);
      additions.push(source);
    }

    if (!additions.length) return;
    update({ tracking_sources: [...sources, ...additions] });
    setBulkSources("");
  }

  async function testSource(index: number) {
    if (!profile || !sources[index]) return;
    setTestingSource(index);
    setSourceTestMessage("");
    setSourceTestError(false);
    try {
      const result = await request<{
        ok: boolean;
        message: string;
        videos: Array<{ video_id: string }>;
      }>(`/api/profiles/${encodeURIComponent(profile.id)}/sources/test`, {
        method: "POST",
        body: { source: sources[index] },
        timeoutMs: 120_000,
      });
      const ids = result.videos.map((video) => video.video_id).join(", ");
      setSourceTestMessage(`${result.message}${ids ? ` ${ids}` : ""}`);
      setSourceTestError(!result.ok);
    } catch (error) {
      setSourceTestMessage(errorText(error));
      setSourceTestError(true);
    } finally {
      setTestingSource(-1);
    }
  }

  return {
    bulkSources,
    setBulkSources,
    testingSource,
    sourceTestMessage,
    sourceTestError,
    sources,
    updateSource,
    updateTikTokUsername,
    addSource,
    removeSource,
    addBulkSources,
    testSource,
  };
}

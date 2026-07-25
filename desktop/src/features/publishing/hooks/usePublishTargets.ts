import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { request } from "../../../shared/api/client";
import { usePolling } from "../../../shared/hooks/usePolling";
import type { PlatformKey, PublishTarget, PublisherProfile, PublisherReadiness } from "../../../shared/types";

type PublishTargetsOptions = {
  setError: Dispatch<SetStateAction<string>>;
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function usePublishTargets({ setError }: PublishTargetsOptions) {
  const profiles = usePolling<{ profiles: PublisherProfile[] }>("/api/publisher/profiles", 10_000);
  const readiness = usePolling<PublisherReadiness>("/api/publisher/readiness", 2_000);
  const [targets, setTargets] = useState<Record<string, PlatformKey[]>>({});
  const [checkingReady, setCheckingReady] = useState(false);
  const readinessRequestRef = useRef("");

  const selectedTargets = useMemo<PublishTarget[]>(
    () =>
      Object.entries(targets)
        .filter(([, platforms]) => platforms.length > 0)
        .map(([profileId, platforms]) => ({ profile_id: profileId, platforms })),
    [targets],
  );
  const selectedTargetSignature = useMemo(
    () =>
      selectedTargets
        .map((target) => `${target.profile_id}:${[...target.platforms].sort().join(",")}`)
        .sort()
        .join("|"),
    [selectedTargets],
  );
  const readinessByKey = useMemo(
    () => new Map((readiness.data?.checks || []).map((check) => [`${check.profile_id}:${check.platform}`, check])),
    [readiness.data],
  );
  const selectedChecks = selectedTargets.flatMap((target) =>
    target.platforms.map((platform) => readinessByKey.get(`${target.profile_id}:${platform}`)),
  );
  const selectedTargetsReady = selectedChecks.length > 0 && selectedChecks.every((check) => check?.ready);
  const selectedTargetsChecking = selectedChecks.some((check) => check?.status === "checking");

  useEffect(() => {
    if (!selectedTargetSignature) {
      readinessRequestRef.current = "";
      return;
    }
    if (readinessRequestRef.current === selectedTargetSignature) return;
    const requestedSignature = selectedTargetSignature;
    readinessRequestRef.current = requestedSignature;
    setCheckingReady(true);
    void request<PublisherReadiness>("/api/publisher/readiness/refresh", {
      method: "POST",
      body: { targets: selectedTargets, force: true },
      timeoutMs: 120_000,
    })
      .then(() => readiness.refresh())
      .catch((error: unknown) => {
        if (readinessRequestRef.current !== requestedSignature) return;
        readinessRequestRef.current = "";
        setError(errorText(error));
      })
      .finally(() => {
        if (readinessRequestRef.current === requestedSignature) setCheckingReady(false);
      });
  }, [selectedTargetSignature, selectedTargets, readiness, setError]);

  function toggleProfile(profile: PublisherProfile) {
    if (!profile.available) return;
    setTargets((current) => {
      if (current[profile.id]) {
        const next = { ...current };
        delete next[profile.id];
        return next;
      }
      const enabled = (Object.keys(profile.platforms) as PlatformKey[]).filter(
        (platform) => profile.platforms[platform],
      );
      return { ...current, [profile.id]: enabled };
    });
  }

  function togglePlatform(profile: PublisherProfile, platform: PlatformKey) {
    if (!profile.platforms[platform] || !targets[profile.id]) return;
    setTargets((current) => {
      const selected = current[profile.id] || [];
      const nextPlatforms = selected.includes(platform)
        ? selected.filter((item) => item !== platform)
        : [...selected, platform];
      return { ...current, [profile.id]: nextPlatforms };
    });
  }

  return {
    profiles,
    targets,
    checkingReady,
    selectedTargets,
    selectedTargetsReady,
    selectedTargetsChecking,
    toggleProfile,
    togglePlatform,
  };
}

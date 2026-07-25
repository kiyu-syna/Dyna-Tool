import { useCallback, useState } from "react";
import { usePolling } from "../../../shared/hooks/usePolling";
import { useI18n } from "../../../shared/i18n";
import type { BrowserProfileSetupState, PublisherReadiness, ProfileSummary } from "../../../shared/types";
import { useBrowserProfileSettings } from "./useBrowserProfileSettings";
import { useBrowserProfiles } from "./useBrowserProfiles";
import { useBrowserSessions } from "./useBrowserSessions";
import { usePublisherLoginStatus } from "./usePublisherLoginStatus";

export function useBrowserAutomation() {
  const { l } = useI18n();
  const summaries = usePolling<{ profiles: ProfileSummary[] }>("/api/profiles", 10_000);
  const setups = usePolling<{ sessions: Record<string, BrowserProfileSetupState> }>(
    "/api/browser-profiles/sessions",
    1_500,
  );
  const readiness = usePolling<PublisherReadiness>("/api/publisher/readiness", 2_000);
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const [busyIds, setBusyIds] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [messageError, setMessageError] = useState(false);

  const setBusy = useCallback((profileId: string, busy: boolean) => {
    setBusyIds((current) =>
      busy
        ? current.includes(profileId)
          ? current
          : [...current, profileId]
        : current.filter((id) => id !== profileId),
    );
  }, []);

  const browserProfiles = useBrowserProfiles({
    summaries: summaries.data?.profiles || [],
    l,
    setBusy,
    setExpandedIds,
    setMessage,
    setMessageError,
  });
  const settings = useBrowserProfileSettings({
    profiles: browserProfiles.profiles,
    setProfiles: browserProfiles.setProfiles,
    l,
    setMessage,
    setMessageError,
  });
  const sessions = useBrowserSessions({
    sessions: setups.data?.sessions || {},
    refreshSessions: setups.refresh,
    reloadProfile: browserProfiles.reloadProfile,
    updateProfile: settings.updateProfile,
    setBusy,
    setExpandedIds,
    setMessage,
    setMessageError,
    l,
  });
  const loginStatus = usePublisherLoginStatus({
    profileList: browserProfiles.profileList,
    readiness: readiness.data,
    refreshReadiness: readiness.refresh,
    setMessage,
    setMessageError,
    l,
  });

  function toggleExpanded(profileId: string) {
    setExpandedIds((current) =>
      current.includes(profileId) ? current.filter((id) => id !== profileId) : [...current, profileId],
    );
  }

  return {
    l,
    summaries,
    setups,
    ...browserProfiles,
    ...settings,
    ...sessions,
    ...loginStatus,
    expandedIds,
    busyIds,
    message,
    messageError,
    toggleExpanded,
  };
}

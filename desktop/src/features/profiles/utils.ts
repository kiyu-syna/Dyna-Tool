import type { ProfileConfig } from "../../shared/types";

export type TrackingSource = NonNullable<ProfileConfig["tracking_sources"]>[number];

export function normalizeTikTokUsername(value: unknown) {
  let username = String(value || "").trim();
  const urlMatch = username.match(/tiktok\.com\/@([^/?#\s]+)/i);
  if (urlMatch?.[1]) username = urlMatch[1];
  return username
    .replace(/^@+/, "")
    .split(/[/?#\s]/)[0]
    .trim();
}

export function tiktokInputValue(source: TrackingSource) {
  const rawUniqueId = String(source.unique_id || "").trim();
  if (rawUniqueId.startsWith("@")) return rawUniqueId;
  const username = normalizeTikTokUsername(rawUniqueId || source.profile_url);
  return username ? `@${username}` : "";
}

export function sourceIdentity(source: TrackingSource) {
  const identity =
    source.platform === "tiktok"
      ? normalizeTikTokUsername(source.unique_id || source.profile_url)
      : String(source.sec_uid || "").trim();
  return identity ? `${source.platform}:${identity.toLocaleLowerCase()}` : "";
}

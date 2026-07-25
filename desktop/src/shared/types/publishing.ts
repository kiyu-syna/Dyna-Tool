import type { PlatformKey } from "./profiles";

export type Job = {
  profile_id: string;
  video_id: string;
  source_label?: string;
  source_key?: string;
  source_platform?: "douyin" | "tiktok";
  video?: {
    share_url?: string;
    [key: string]: unknown;
  };
  status: string;
  caption?: string;
  scheduled_at?: string;
  enabled_platforms?: PlatformKey[];
  platforms?: Record<string, { status?: string; last_error?: string }>;
  active?: boolean;
  last_error?: string;
  created_at?: string;
  updated_at?: string;
};

export type PublisherProfile = {
  id: string;
  name: string;
  enabled: boolean;
  available: boolean;
  platforms: Record<PlatformKey, boolean>;
  reason: string;
  readiness?: Partial<Record<PlatformKey, PublisherReadyCheck>>;
};

export type PublisherReadyCheck = {
  profile_id: string;
  platform: PlatformKey;
  status: "unknown" | "checking" | "ready" | "login_required" | "attention" | "error" | string;
  ready: boolean;
  message: string;
  checked_at: string;
  expires_at: string;
};

export type PublisherReadiness = {
  checks: PublisherReadyCheck[];
  checking: boolean;
  checking_profiles: string[];
};

export type PublishTarget = {
  profile_id: string;
  platforms: PlatformKey[];
};

export type PublishBatchResult = {
  ok: boolean;
  batch_id: string;
  file_count: number;
  job_count: number;
  scheduled_count: number;
  immediate_count: number;
};

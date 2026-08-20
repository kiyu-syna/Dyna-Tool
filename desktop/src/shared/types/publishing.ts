import type { PlatformKey } from "./profiles";

export type Job = {
  profile_id: string;
  video_id: string;
  batch_id?: string;
  batch_name?: string;
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
  batch_name?: string;
  file_count: number;
  job_count: number;
  scheduled_count: number;
  immediate_count: number;
};

export type DouyinSelectionItem = {
  video_id: string;
  source_url: string;
  description: string;
  author_uid?: string;
  author_nickname?: string;
  create_time?: number;
  duration_ms?: number;
  thumbnail_url?: string;
  selected_order: number;
  status?: string;
  last_error?: string;
};

export type DouyinSelectionSession = {
  id: string;
  source_url: string;
  status: "selecting" | "ready" | "preparing" | "published" | "cancelled" | "expired" | string;
  items: DouyinSelectionItem[];
  selected_count: number;
  focus_requested?: boolean;
  created_at: string;
  updated_at: string;
  expires_at: string;
  last_error?: string;
  published_count?: number;
  failed_count?: number;
};

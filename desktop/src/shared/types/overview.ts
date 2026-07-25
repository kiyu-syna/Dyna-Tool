import type { PlatformKey } from "./profiles";

export type Overview = {
  generated_at: string;
  kpis: Record<string, number>;
  daily: Array<{
    date: string;
    label: string;
    detected: number;
    completed: number;
    restricted: number;
    publish_error: number;
  }>;
  platforms: Array<{
    key: PlatformKey;
    label: string;
    success: number;
    failed: number;
    pending: number;
    rate: number;
  }>;
  profiles: Array<{
    profile_id: string;
    name: string;
    enabled: boolean;
    running: boolean;
    health: string;
    recovery_count: number;
    queue: number;
    errors: number;
    current_video: string;
    last_activity: string;
  }>;
  errors: Array<{
    profile_id: string;
    video_id: string;
    status: string;
    error: string;
    updated_at: string;
  }>;
  recent_activity?: Array<{
    occurred_at: string;
    profile_id: string;
    video_id: string;
    event_type: string;
    status: string;
    platform: string;
    error: string;
  }>;
};

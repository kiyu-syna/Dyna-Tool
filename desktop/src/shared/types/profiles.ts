// Shared contracts exchanged between the Electron UI and the local backend.
export type PlatformKey = "tiktok" | "youtube" | "facebook";

export type ProfileSummary = {
  id: string;
  name: string;
  enabled: boolean;
  source_count: number;
  enabled_source_count: number;
  source_platforms?: Record<"douyin" | "tiktok", number>;
  check_interval_minutes: number;
  platforms: Record<PlatformKey, boolean>;
  running: boolean;
  current_video: string;
  queue_count: number;
  error_count: number;
};

export type RuntimeProfileState = {
  profile_id: string;
  status: "stopped" | "starting" | "running" | "degraded" | "stopping" | "error" | string;
  active: boolean;
  message: string;
  current_source: string;
  source_count: number;
  healthy_source_count: number;
  failing_source_count: number;
  last_successful_scan_at: string;
  source_health: Record<
    string,
    {
      source_key: string;
      platform: string;
      label: string;
      last_attempt_at: string;
      last_success_at?: string;
      attempt_count: number;
      consecutive_failures: number;
      last_error: string;
    }
  >;
  started_at: string;
  updated_at: string;
  last_error: string;
  resources?: {
    ram_mb: number;
    cpu_percent: number;
    process_count: number;
    browser_pids: number[];
    browser_provider?: "gemlogin" | "local_chromium";
    gemlogin_ids: string[];
    debug_addresses: string[];
  };
};

export type RuntimeSnapshot = {
  profiles: Record<string, RuntimeProfileState>;
  active_profile_ids: string[];
  resources?: {
    generated_at: string;
    system: {
      ram_percent?: number;
      ram_used_mb?: number;
      ram_available_mb?: number;
      cpu_percent?: number;
    };
    workload: Record<
      string,
      {
        limit: number;
        active_count: number;
        waiting_count: number;
      }
    >;
  };
};

export type TestUploadState = {
  profile_id: string;
  status: string;
  active: boolean;
  message: string;
  video_id: string;
  platforms: PlatformKey[];
  results: Partial<Record<PlatformKey, { ok: boolean; message: string }>>;
  last_error: string;
  started_at: string;
  updated_at: string;
};

export type BrowserDiagnostic = {
  event_id: string;
  occurred_at: string;
  profile_id: string;
  video_id: string;
  platform: string;
  url: string;
  error: string;
  last_response: Record<string, unknown>;
  screenshot_available: boolean;
  screenshot_error: string;
};

export type BrowserDiagnosticHistory = {
  items: BrowserDiagnostic[];
};

export type ProfileConfig = {
  id: string;
  name: string;
  enabled: boolean;
  default_caption?: string;
  check_interval_minutes?: number;
  processing_priority?: number;
  initial_scan_mode?: "skip_existing" | "process_latest";
  save_dir?: string;
  browser?: {
    provider?: "gemlogin" | "local_chromium";
    user_data_dir?: string;
    executable_path?: string;
    profile_directory?: string;
    headless?: boolean;
    background?: boolean;
    launch_timeout_ms?: number;
    login_mode?: "native" | "playwright";
    fingerprint_mode?: "system";
    proxy?: {
      enabled?: boolean;
      server?: string;
      username?: string;
      password?: string;
      password_set?: boolean;
      bypass?: string;
    };
    [key: string]: unknown;
  };
  douyin?: {
    gemlogin_profile_id?: string;
    [key: string]: unknown;
  };
  tracking_sources?: Array<{
    platform: "douyin" | "tiktok";
    display_name?: string;
    profile_url?: string;
    sec_uid?: string;
    unique_id?: string;
    enabled?: boolean;
    check_interval_minutes?: number;
    [key: string]: unknown;
  }>;
  filters?: { min_likes?: number; max_duration_seconds?: number };
  caption_options?: {
    tiktok_use_original_desc?: boolean;
    douyin_use_original_desc?: boolean;
    telegram_use_custom_caption?: boolean;
    telegram_pin_caption_message?: boolean;
  };
  tiktok?: {
    enabled?: boolean;
    gemlogin_profile_id?: string;
    use_original_desc?: boolean;
    [key: string]: unknown;
  };
  youtube?: {
    enabled?: boolean;
    gemlogin_profile_id?: string;
    use_original_desc?: boolean;
    channel_id?: string;
    preset?: string;
    crf?: number;
    [key: string]: unknown;
  };
  facebook?: {
    enabled?: boolean;
    gemlogin_profile_id?: string;
    use_original_desc?: boolean;
    profile_url?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

export type SeenSource = {
  source_key: string;
  label: string;
  platform?: "douyin" | "tiktok";
  exists: boolean;
  seen_count: number;
  last_check?: string | null;
};

export type DiagnosticsResult = {
  api_url: string;
  telegram: { ok: boolean; message: string };
  ffprobe: { ok: boolean; path: string; message: string };
  profiles: Array<{
    profile_id: string;
    name: string;
    gemlogin_profile_id: string;
    browser_provider?: "gemlogin" | "local_chromium";
    browser_label?: string;
    source_count: number;
    platforms: string[];
    ok: boolean;
    message: string;
  }>;
};

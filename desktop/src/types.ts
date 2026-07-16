export type PlatformKey = "tiktok" | "youtube" | "facebook";

export type ProfileSummary = {
  id: string;
  name: string;
  enabled: boolean;
  source_count: number;
  enabled_source_count: number;
  check_interval_minutes: number;
  platforms: Record<PlatformKey, boolean>;
  running: boolean;
  current_video: string;
  queue_count: number;
  error_count: number;
};

export type RuntimeProfileState = {
  profile_id: string;
  status: "stopped" | "starting" | "running" | "stopping" | "error" | string;
  active: boolean;
  message: string;
  current_source: string;
  source_count: number;
  started_at: string;
  updated_at: string;
  last_error: string;
  resources?: {
    ram_mb: number;
    cpu_percent: number;
    process_count: number;
    browser_pids: number[];
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
    workload: Record<string, {
      limit: number;
      active_count: number;
      waiting_count: number;
    }>;
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

export type ProfileConfig = {
  id: string;
  name: string;
  enabled: boolean;
  default_caption?: string;
  check_interval_minutes?: number;
  processing_priority?: number;
  save_dir?: string;
  douyin?: {
    gemlogin_profile_id?: string;
    sources?: Array<{
      target_sec_uid: string;
      target_display_name?: string;
      enabled?: boolean;
      [key: string]: unknown;
    }>;
    [key: string]: unknown;
  };
  filters?: { min_likes?: number; max_duration_seconds?: number };
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
    source_count: number;
    platforms: string[];
    ok: boolean;
    message: string;
  }>;
};

export type Job = {
  profile_id: string;
  video_id: string;
  source_label?: string;
  status: string;
  caption?: string;
  enabled_platforms?: PlatformKey[];
  platforms?: Record<string, { status?: string; last_error?: string }>;
  active?: boolean;
  last_error?: string;
  created_at?: string;
  updated_at?: string;
};

export type Overview = {
  generated_at: string;
  kpis: Record<string, number>;
  daily: Array<{ date: string; label: string; detected: number; completed: number; failed: number }>;
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
};

export type AppSettings = {
  API_URL?: string;
  PAYMENT_API_URL?: string;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
  TELEGRAM_QUIET_HOURS_ENABLED?: boolean;
  TELEGRAM_QUIET_START?: string;
  TELEGRAM_QUIET_END?: string;
  MAX_CONCURRENT_DOWNLOADS?: number;
  MAX_CONCURRENT_FFMPEG?: number;
  MAX_CONCURRENT_UPLOADS?: number;
  UI_THEME?: "light" | "dark";
  UI_LANGUAGE?: "vi" | "en";
};

export type BusyModeState = {
  busy: boolean;
  source: string;
  updated_at: string;
};

export type AuthUser = {
  username: string;
  phone?: string;
  display_name?: string;
};

export type AuthStatus = {
  authenticated: boolean;
  user: AuthUser | null;
};

export type LicensePlan = {
  days: number;
  label: string;
  price: number;
  per_day: string;
  tag?: string;
};

export type LicenseInfo = {
  username?: string;
  is_active?: boolean;
  plan_name?: string;
  expires_at?: string;
  days_remaining?: number;
  source?: string;
  error?: string;
};

export type LicenseStatus = {
  is_active: boolean;
  info: LicenseInfo;
  plans: LicensePlan[];
};

export type PaymentOrder = {
  order_id: string;
  username: string;
  days: number;
  plan_name: string;
  amount: number;
  transfer_content: string;
  qr_url: string;
  bank_id: string;
  account_no: string;
  account_name: string;
  expires_at_order: string;
  status: "pending" | "paid" | "expired" | "cancelled" | string;
};

export type PaymentStatus = {
  order_id: string;
  status: "pending" | "paid" | "expired" | "cancelled" | string;
  amount: number;
  paid_at?: string | null;
  subscription_expires_at?: string | null;
  plan_name: string;
};

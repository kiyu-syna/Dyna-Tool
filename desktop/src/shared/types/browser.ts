export type BrowserRuntimeInfo = {
  runtime_id: string;
  root_dir: string;
  executable_path: string;
  source_executable: string;
  installed_at: string;
  file_count: number;
  size_bytes: number;
  executable_sha256: string;
  reused: boolean;
  valid: boolean;
  error?: string;
};

export type BrowserProfileSetupState = {
  profile_id: string;
  status: "starting" | "running" | "closing" | "completed" | "error" | string;
  active: boolean;
  message: string;
  user_data_dir: string;
  executable_path: string;
  profile_directory: string;
  mode?: "create" | "existing";
  login_mode?: "native" | "playwright";
  fingerprint_mode?: "system";
  browser_name?: string;
  sessions: Partial<Record<"facebook" | "tiktok" | "youtube" | "douyin", boolean>>;
  last_error: string;
  started_at: string;
  updated_at: string;
  completed_at?: string;
  error_code?: string;
  suggested_action?: string;
  recovery_mode?: "safe_mode" | string;
};

export type BrowserProfileCheckState = {
  profile_id: string;
  status: "ready" | "recovered" | "in_use" | "blocked" | "invalid" | string;
  code: string;
  ready: boolean;
  message: string;
  suggested_action: string;
  locks: string[];
  pids: number[];
  repaired: boolean;
  recovery_path: string;
  checked_at: string;
};

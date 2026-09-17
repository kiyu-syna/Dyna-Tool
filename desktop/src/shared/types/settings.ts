export type AppSettings = {
  API_URL?: string;
  MAX_CONCURRENT_DOWNLOADS?: number;
  MAX_CONCURRENT_FFMPEG?: number;
  MAX_CONCURRENT_UPLOADS?: number;
  TELEGRAM_NOTIFICATION_TYPES?: Partial<
    Record<"new_video" | "upload_success" | "upload_failure" | "high_ram" | "job_confirmation", boolean>
  >;
  UI_THEME?: "system" | "light" | "dark";
  UI_LANGUAGE?: "vi" | "en" | "zh" | "zh-TW";
};

import type { Job, PlatformKey } from "../../shared/types";

export const PLATFORM_LABELS: Record<PlatformKey, string> = {
  tiktok: "TikTok",
  youtube: "YouTube Shorts",
  facebook: "Facebook Reels",
};

export type PublishItemDraft = {
  file_path: string;
  caption: string;
  scheduled_at: string;
};

export function fileName(filePath: string) {
  return filePath.split(/[\\/]/).pop() || filePath;
}

export function directoryName(filePath: string) {
  const parts = filePath.split(/[\\/]/);
  return parts.slice(0, -1).join("\\");
}

export function jobTone(status: string) {
  if (status === "completed") return "success" as const;
  if (status.startsWith("failed")) return "danger" as const;
  if (["uploading", "downloading", "importing"].includes(status)) return "info" as const;
  if (["cancelled", "ignored"].includes(status)) return "neutral" as const;
  return "warning" as const;
}

export function jobLabel(status: string, l: (vi: string, en: string, zh: string) => string) {
  return (
    (
      {
        detected: l("Đã nhận", "Received", "已接收"),
        caption_ready: l("Đã có mô tả", "Caption ready", "文案已就绪"),
        importing: l("Đang sao chép", "Copying", "正在复制"),
        scheduled: l("Đã đặt lịch", "Scheduled", "已排期"),
        downloaded: l("Sẵn sàng đăng", "Ready to publish", "可发布"),
        uploading: l("Đang đăng", "Publishing", "正在发布"),
        completed: l("Hoàn tất", "Completed", "已完成"),
        cancelled: l("Đã hủy", "Cancelled", "已取消"),
        ignored: l("Đã bỏ qua", "Ignored", "已忽略"),
        failed: l("Lỗi", "Failed", "失败"),
        failed_download: l("Lỗi nhận file", "File import failed", "文件导入失败"),
        failed_upload: l("Lỗi đăng", "Publish failed", "发布失败"),
      } as Record<string, string>
    )[status] || status
  );
}

export function minimumScheduleValue() {
  const date = new Date(Date.now() + 120_000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

export function localDateValue(date: Date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function localDateTimeValue(date: Date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

export function defaultScheduleDate() {
  return localDateValue(new Date(Date.now() + 24 * 60 * 60 * 1000));
}

export function parseScheduleTimes(value: string) {
  const matches = value
    .split(/[,;\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const valid = matches.filter((item) => /^([01]\d|2[0-3]):[0-5]\d$/.test(item));
  return [...new Set(valid)].sort();
}

export function sourceFileName(job: Job) {
  const label = String(job.source_label || "");
  return label.includes(" · ") ? label.split(" · ").slice(1).join(" · ") : label || job.video_id;
}

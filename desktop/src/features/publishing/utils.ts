import type { Job, PlatformKey } from "../../shared/types";

export const PLATFORM_LABELS: Record<PlatformKey, string> = {
  tiktok: "TikTok",
  youtube: "YouTube Shorts",
  facebook: "Facebook Reels",
};

export type PublishItemDraft = {
  file_path: string;
  caption: string;
  original_description?: string;
  scheduled_at: string;
  source_type?: "local" | "douyin";
  selection_id?: string;
  video_id?: string;
  source_url?: string;
  thumbnail_url?: string;
  author_nickname?: string;
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

export function defaultBatchName(now = new Date()) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `Lô video ${pad(now.getDate())}/${pad(now.getMonth() + 1)}/${now.getFullYear()} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

export function parseScheduleTimes(value: string) {
  const matches = value
    .split(/[,;\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const valid = matches.filter((item) => /^([01]\d|2[0-3]):[0-5]\d$/.test(item));
  return [...new Set(valid)].sort();
}

function minuteOfDay(value: string) {
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) return -1;
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
}

export function buildRandomSchedule(
  itemCount: number,
  startDate: string,
  videosPerDay: number,
  fromTime: string,
  toTime: string,
  options: { now?: number; random?: () => number } = {},
) {
  if (itemCount <= 0) return [];
  const fromMinute = minuteOfDay(fromTime);
  const toMinute = minuteOfDay(toTime);
  if (fromMinute < 0 || toMinute < 0 || toMinute <= fromMinute) return [];
  const perDay = Math.max(1, Math.min(10, Math.trunc(videosPerDay || 1)));
  if (toMinute - fromMinute + 1 < perDay) return [];
  const start = new Date(`${startDate}T00:00:00`);
  if (!Number.isFinite(start.getTime())) return [];

  const random = options.random || Math.random;
  const minimum = (options.now ?? Date.now()) + 120_000;
  const maximum = (options.now ?? Date.now()) + 365 * 24 * 60 * 60 * 1000;
  const values: string[] = [];
  let dayOffset = 0;
  while (values.length < itemCount && dayOffset <= 365) {
    const candidates: number[] = [];
    for (let minute = fromMinute; minute <= toMinute; minute += 1) {
      const candidate = new Date(start);
      candidate.setDate(start.getDate() + dayOffset);
      candidate.setHours(Math.floor(minute / 60), minute % 60, 0, 0);
      const timestamp = candidate.getTime();
      if (timestamp > minimum && timestamp <= maximum) candidates.push(timestamp);
    }
    for (let index = candidates.length - 1; index > 0; index -= 1) {
      const swapIndex = Math.floor(Math.max(0, Math.min(0.999999, random())) * (index + 1));
      [candidates[index], candidates[swapIndex]] = [candidates[swapIndex], candidates[index]];
    }
    const daily = candidates.slice(0, Math.min(perDay, itemCount - values.length)).sort((left, right) => left - right);
    values.push(...daily.map((timestamp) => localDateTimeValue(new Date(timestamp))));
    dayOffset += 1;
  }
  return values.length === itemCount ? values : [];
}

export function sourceFileName(job: Job) {
  const label = String(job.source_label || "");
  return label.includes(" · ") ? label.split(" · ").slice(1).join(" · ") : label || job.video_id;
}

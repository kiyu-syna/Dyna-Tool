import { ArrowRight, Ban, ExternalLink, RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";
import { formatDateTime, request } from "../api/client";
import { usePolling } from "../hooks/usePolling";
import { useI18n } from "../i18n";
import type { Job, PlatformKey } from "../types";
import { EmptyState, PlatformMarks, Section, SkeletonRows, StatusPill } from "./Common";

const PLATFORM_KEYS: PlatformKey[] = ["tiktok", "youtube", "facebook"];
const TERMINAL_STATUSES = new Set(["completed", "cancelled", "ignored"]);

export type QueueFilter = "all" | "processing" | "success" | "failed";

type VideoQueueSectionProps = {
  onChanged?: () => void | Promise<unknown>;
  maxRows?: number;
  showFilters?: boolean;
  showReset?: boolean;
  filter?: QueueFilter;
  onFilterChange?: (filter: QueueFilter) => void;
  onViewAll?: () => void;
};

function failedPlatforms(job: Job): PlatformKey[] {
  if (!job.status.startsWith("failed")) return [];
  return PLATFORM_KEYS.filter((platform) => job.platforms?.[platform]?.status === "failed");
}

function uploadingPlatforms(job: Job): PlatformKey[] {
  return PLATFORM_KEYS.filter((platform) => job.platforms?.[platform]?.status === "uploading");
}

function videoUrl(job: Job): string {
  const videoId = String(job.video_id || "").trim();
  const value = String(job.video?.share_url || "").trim();
  if (/^https?:\/\//i.test(value)) {
    if (job.source_platform === "tiktok" && /tiktok\.com/i.test(value)) return value;
    if (
      job.source_platform !== "tiktok" &&
      (/\/video\//i.test(value) || /\/v\//i.test(value) || /v\.douyin\.com/i.test(value))
    )
      return value;
  }
  if (!/^\d{8,}$/.test(videoId)) return "";
  return job.source_platform === "tiktok"
    ? `https://www.tiktok.com/video/${encodeURIComponent(videoId)}`
    : `https://www.douyin.com/video/${encodeURIComponent(videoId)}`;
}

function jobTone(status: string) {
  if (status === "completed") return "success" as const;
  if (status.startsWith("failed")) return "danger" as const;
  if (["uploading", "downloading"].includes(status)) return "info" as const;
  if (status === "cancelled") return "neutral" as const;
  return "warning" as const;
}

function jobLabel(status: string, l: (vi: string, en: string, zh: string) => string) {
  return (
    (
      {
        detected: l("Mới phát hiện", "Detected", "新发现"),
        waiting_caption: l("Chờ mô tả", "Waiting for caption", "等待文案"),
        caption_ready: l("Đã có mô tả", "Caption ready", "文案已就绪"),
        downloading: l("Đang tải", "Downloading", "正在下载"),
        downloaded: l("Đã tải", "Downloaded", "已下载"),
        uploading: l("Đang đăng", "Publishing", "正在发布"),
        completed: l("Hoàn tất", "Completed", "已完成"),
        cancelled: l("Đã hủy", "Cancelled", "已取消"),
        ignored: l("Đã bỏ qua", "Ignored", "已忽略"),
        failed: l("Lỗi", "Failed", "失败"),
        failed_download: l("Lỗi tải", "Download failed", "下载失败"),
        failed_upload: l("Lỗi đăng", "Publish failed", "发布失败"),
      } as Record<string, string>
    )[status] || status
  );
}

export default function VideoQueueSection({
  onChanged,
  maxRows = Number.POSITIVE_INFINITY,
  showFilters = false,
  showReset = true,
  filter: controlledFilter,
  onFilterChange,
  onViewAll,
}: VideoQueueSectionProps) {
  const { l } = useI18n();
  const jobs = usePolling<{ jobs: Job[]; total: number }>("/api/jobs?limit=500", 3000);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [localFilter, setLocalFilter] = useState<QueueFilter>("all");
  const activeFilter = controlledFilter ?? localFilter;

  const queueRows = (jobs.data?.jobs || []).map((job, index) => ({ job, ordinal: (jobs.data?.total || 0) - index }));
  const filteredRows = queueRows.filter(({ job }) => {
    if (activeFilter === "failed") return job.status.startsWith("failed");
    if (activeFilter === "success") return job.status === "completed";
    if (activeFilter === "processing") return !job.status.startsWith("failed") && !TERMINAL_STATUSES.has(job.status);
    return true;
  });
  const visibleRows = filteredRows.slice(0, maxRows);

  function changeFilter(nextFilter: QueueFilter) {
    setLocalFilter(nextFilter);
    onFilterChange?.(nextFilter);
  }

  async function refreshAfterChange() {
    await jobs.refresh();
    await onChanged?.();
  }

  async function act(action: "retry" | "cancel", job: Job) {
    setBusyAction(`job-${job.profile_id}-${job.video_id}-${action}`);
    setError("");
    try {
      await request(`/api/jobs/${action}`, {
        method: "POST",
        body: { profile_id: job.profile_id, video_id: job.video_id },
      });
      await refreshAfterChange();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function openVideo(job: Job) {
    const url = videoUrl(job);
    if (!url) return;
    setError("");
    try {
      if (!window.dyna?.openExternal)
        throw new Error(
          l(
            "Ứng dụng cần được mở lại để dùng nút Xem video.",
            "Restart the app to use View video.",
            "请重启应用后使用查看视频按钮。",
          ),
        );
      await window.dyna.openExternal(url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function resetQueue() {
    if (
      !jobs.data?.total ||
      !window.confirm(
        l(
          `Reset toàn bộ ${jobs.data.total} video trong hàng đợi? File video gốc sẽ được giữ nguyên.`,
          `Reset all ${jobs.data.total} videos in the queue? Original video files will be kept.`,
          `重置队列中的全部 ${jobs.data.total} 个视频？原始视频文件将保留。`,
        ),
      )
    )
      return;
    setBusyAction("queue-reset");
    setError("");
    try {
      await request("/api/jobs/reset", { method: "POST" });
      await refreshAfterChange();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }

  const action = (
    <div className="queue-section-actions">
      {onViewAll && (
        <button className="small-button primary" onClick={onViewAll}>
          {l("Sang tab Theo dõi", "Open Tracking", "前往跟踪")}
          <ArrowRight size={14} />
        </button>
      )}
      {showReset && (
        <button
          className="small-button danger queue-reset-button"
          disabled={!jobs.data?.total || Boolean(busyAction)}
          onClick={() => void resetQueue()}
          title={
            !jobs.data?.total
              ? l("Hàng đợi đang trống", "The queue is empty", "队列为空")
              : l("Xóa sạch toàn bộ hàng đợi", "Clear the entire queue", "清空整个队列")
          }
        >
          <Trash2 size={14} />
          {busyAction === "queue-reset"
            ? l("Đang reset...", "Resetting...", "正在重置...")
            : !jobs.data?.total
              ? l("Hàng đợi trống", "Queue empty", "队列为空")
              : l("Reset hàng đợi", "Reset queue", "重置队列")}
        </button>
      )}
    </div>
  );

  return (
    <Section
      title={`${l("Hàng đợi video", "Video queue", "视频队列")}${jobs.data ? ` · ${activeFilter === "all" ? jobs.data.total : filteredRows.length}` : ""}`}
      action={action}
    >
      {error && <div className="action-error">{error}</div>}
      {showFilters && (
        <div className="queue-filter-bar">
          {(["all", "processing", "success", "failed"] as QueueFilter[]).map((item) => (
            <button
              key={item}
              className={`queue-filter-${item} ${activeFilter === item ? "active" : ""}`}
              onClick={() => changeFilter(item)}
            >
              {item === "all"
                ? l("Tất cả", "All", "全部")
                : item === "processing"
                  ? l("Đang xử lý", "Processing", "处理中")
                  : item === "success"
                    ? l("Thành công", "Successful", "成功")
                    : l("Thất bại", "Failed", "失败")}
            </button>
          ))}
          <span>
            {Number.isFinite(maxRows)
              ? l(
                  `Hiển thị ${maxRows} video gần nhất`,
                  `Showing the ${maxRows} most recent videos`,
                  `显示最近 ${maxRows} 个视频`,
                )
              : l("Hiển thị toàn bộ hàng đợi", "Showing the full queue", "显示完整队列")}
          </span>
        </div>
      )}
      {jobs.loading && !jobs.data ? (
        <SkeletonRows />
      ) : jobs.error && !jobs.data ? (
        <EmptyState error message={jobs.error} />
      ) : !visibleRows.length ? (
        <EmptyState
          message={
            activeFilter === "all"
              ? l("Chưa có video trong hàng đợi", "No videos in the queue", "队列中没有视频")
              : l("Không có video phù hợp bộ lọc", "No videos match this filter", "没有符合筛选条件的视频")
          }
        />
      ) : (
        <div className="table-wrap queue-table">
          <table>
            <thead>
              <tr>
                <th className="queue-index-column">STT</th>
                <th>{l("Hồ sơ", "Profile", "配置文件")}</th>
                <th>Video</th>
                <th>{l("Nguồn", "Source", "来源")}</th>
                <th>{l("Nền tảng", "Platforms", "平台")}</th>
                <th>{l("Trạng thái", "Status", "状态")}</th>
                <th>{l("Cập nhật", "Updated", "更新时间")}</th>
                <th>{l("Thao tác", "Actions", "操作")}</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map(({ job, ordinal }) => (
                <tr key={`${job.profile_id}:${job.video_id}`}>
                  <td className="queue-index-column">{ordinal}</td>
                  <td>P{job.profile_id}</td>
                  <td>
                    <div className="queue-video-cell">
                      {videoUrl(job) ? (
                        <button
                          className="queue-video-link"
                          onClick={() => void openVideo(job)}
                          title={l(
                            "Mở video bằng trình duyệt mặc định",
                            "Open the video in your default browser",
                            "使用默认浏览器打开视频",
                          )}
                        >
                          <ExternalLink size={13} />
                          {l("Xem video", "View video", "查看视频")}
                        </button>
                      ) : (
                        <span>—</span>
                      )}
                    </div>
                  </td>
                  <td>
                    <div className="queue-source-cell">
                      {job.source_platform && (
                        <span className={`source-platform-badge ${job.source_platform}`}>
                          {job.source_platform === "tiktok" ? "TikTok" : "Douyin"}
                        </span>
                      )}
                      <span>{job.source_label || "-"}</span>
                    </div>
                  </td>
                  <td>
                    <div
                      className={`queue-platform-status ${uploadingPlatforms(job)
                        .map((platform) => `uploading-${platform}`)
                        .join(" ")}`}
                    >
                      <PlatformMarks platforms={job.enabled_platforms || []} failedPlatforms={failedPlatforms(job)} />
                    </div>
                  </td>
                  <td>
                    <StatusPill text={jobLabel(job.status, l)} tone={jobTone(job.status)} />
                  </td>
                  <td>{formatDateTime(job.updated_at)}</td>
                  <td>
                    <div className="row-actions">
                      {job.status.startsWith("failed") && (
                        <button
                          className="small-button"
                          disabled={Boolean(busyAction)}
                          onClick={() => void act("retry", job)}
                        >
                          <RotateCcw size={14} />
                          {l("Thử lại", "Retry", "重试")}
                        </button>
                      )}
                      {(!job.active || job.status === "uploading") &&
                        !["completed", "cancelled", "ignored"].includes(job.status) && (
                          <button
                            className="small-button danger"
                            disabled={Boolean(busyAction)}
                            onClick={() => void act("cancel", job)}
                          >
                            <Ban size={14} />
                            {l("Hủy", "Cancel", "取消")}
                          </button>
                        )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

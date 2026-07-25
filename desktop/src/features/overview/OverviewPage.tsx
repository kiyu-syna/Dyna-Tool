import { Activity, AlertCircle, CheckCircle2, Clock3, Cpu, Gauge, MemoryStick, PlayCircle, Video } from "lucide-react";
import { useRef, useState } from "react";
import { formatDateTime } from "../../shared/api/client";
import { EmptyState, Section, SkeletonRows } from "../../shared/components/Common";
import VideoQueueSection, { type QueueFilter } from "../../shared/components/VideoQueueSection";
import { usePolling } from "../../shared/hooks/usePolling";
import { useI18n } from "../../shared/i18n";
import type { Overview, RuntimeSnapshot } from "../../shared/types";
import "./overview.css";

const metrics = [
  ["detected_today", ["Phát hiện hôm nay", "Detected today", "今日发现"], Video, "blue"],
  ["completed_today", ["Hoàn tất hôm nay", "Completed today", "今日完成"], CheckCircle2, "green"],
  ["waiting", ["Đang chờ xử lý", "Waiting / processing", "等待处理"], Clock3, "amber"],
  ["restricted", ["Bị hạn chế", "Restricted", "受限"], AlertCircle, "amber"],
  ["publish_errors", ["Lỗi đăng", "Publishing errors", "发布错误"], AlertCircle, "red"],
  ["success_rate", ["Tỷ lệ thành công", "Success rate", "成功率"], Gauge, "violet"],
] as const;

const platformNames: Record<string, string> = {
  tiktok: "TikTok",
  youtube: "YouTube",
  facebook: "Facebook",
};

function resourceTone(value: number) {
  if (value > 90) return "health-critical";
  if (value >= 75) return "health-warning";
  return "health-good";
}

function successRateTone(value: number) {
  if (value > 90) return "rate-good";
  if (value >= 70) return "rate-warning";
  return "rate-critical";
}

function activityText(data: Overview, l: (vi: string, en: string, zh: string) => string) {
  const item = data.recent_activity?.[0];
  if (!item) return l("Chưa có hoạt động mới", "No recent activity", "暂无最近活动");
  const profile = data.profiles.find((entry) => entry.profile_id === item.profile_id);
  const profileName = profile?.name || `Profile ${item.profile_id}`;
  const platform = platformNames[item.platform] || item.platform;
  if (item.event_type === "platform" && item.status === "success")
    return l(
      `${profileName} vừa đăng ${platform} thành công`,
      `${profileName} just published successfully to ${platform}`,
      `${profileName} 刚刚成功发布到 ${platform}`,
    );
  if (item.event_type === "platform" && item.status === "failed")
    return l(
      `${profileName} đăng ${platform} thất bại`,
      `${profileName} failed to publish to ${platform}`,
      `${profileName} 发布到 ${platform} 失败`,
    );
  if (item.event_type === "detected")
    return l(
      `${profileName} vừa phát hiện video mới`,
      `${profileName} just detected a new video`,
      `${profileName} 刚发现新视频`,
    );
  if (item.status === "completed")
    return l(
      `${profileName} vừa hoàn tất một video`,
      `${profileName} just completed a video`,
      `${profileName} 刚完成一个视频`,
    );
  if (item.status.startsWith("failed"))
    return l(
      `${profileName} vừa gặp lỗi khi xử lý video`,
      `${profileName} encountered a video processing error`,
      `${profileName} 处理视频时遇到错误`,
    );
  return l(
    `${profileName} vừa cập nhật trạng thái video`,
    `${profileName} just updated a video`,
    `${profileName} 刚更新了视频状态`,
  );
}

export default function OverviewPage({ onOpenTracking }: { onOpenTracking: () => void }) {
  const { l } = useI18n();
  const overview = usePolling<Overview>("/api/overview", 5000);
  const runtime = usePolling<RuntimeSnapshot>("/api/runtime", 2000);
  const [queueFilter, setQueueFilter] = useState<QueueFilter>("all");
  const [selectedDay, setSelectedDay] = useState("");
  const queueRef = useRef<HTMLDivElement>(null);
  const { data, error, loading } = overview;
  if (loading && !data) return <SkeletonRows count={8} />;
  if (error && !data) return <EmptyState error message={error} />;
  if (!data) return null;

  const maxDaily = Math.max(1, ...data.daily.flatMap((day) => [day.completed, day.restricted, day.publish_error]));
  const runningProfiles =
    runtime.data?.active_profile_ids.length ?? data.profiles.filter((profile) => profile.running).length;
  const ram = Math.round(Number(runtime.data?.resources?.system.ram_percent || 0));
  const cpu = Math.round(Number(runtime.data?.resources?.system.cpu_percent || 0));
  const latestActivity = data.recent_activity?.[0];
  const activeDay = data.daily.find((day) => day.date === selectedDay) || data.daily[data.daily.length - 1];

  function showQueue(filter: QueueFilter) {
    setQueueFilter(filter);
    window.setTimeout(() => queueRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  return (
    <div className="page-stack overview-page">
      <div className="metrics-grid">
        {metrics.map(([key, labels, Icon, color]) => {
          const target: QueueFilter =
            key === "restricted" || key === "publish_errors"
              ? "failed"
              : key === "completed_today"
                ? "success"
                : key === "waiting"
                  ? "processing"
                  : "all";
          const successRate = Number(data.kpis.success_rate || 0);
          const rateTone = successRateTone(successRate);
          const metricColor =
            key === "success_rate"
              ? rateTone === "rate-good"
                ? "green"
                : rateTone === "rate-warning"
                  ? "amber"
                  : "red"
              : color;
          const content = (
            <>
              <span className={`metric-icon ${metricColor}`}>
                <Icon size={18} />
              </span>
              <div>
                <p>{l(labels[0], labels[1], labels[2])}</p>
                <strong className={key === "completed_today" ? "success-value" : undefined}>
                  {data.kpis[key] ?? 0}
                  {key === "success_rate" ? "%" : ""}
                </strong>
              </div>
            </>
          );
          if (key === "success_rate")
            return (
              <article className={`metric-card overview-metric-card is-static success-rate-card ${rateTone}`} key={key}>
                {content}
              </article>
            );
          return (
            <button
              className={`metric-card overview-metric-card ${(key === "restricted" || key === "publish_errors") && Number(data.kpis[key] || 0) > 0 ? "has-error" : ""}`}
              key={key}
              onClick={() => showQueue(target)}
              title={l("Bấm để xem danh sách liên quan", "Click to view the related list", "点击查看相关列表")}
            >
              {content}
            </button>
          );
        })}
      </div>

      <section className="system-health-strip" aria-label={l("Sức khỏe hệ thống", "System health", "系统健康")}>
        <div className="system-health-title">
          <Activity size={18} />
          <div>
            <strong>{l("Sức khỏe hệ thống", "System health", "系统健康")}</strong>
            <span>{l("Cập nhật theo thời gian thực", "Updated in real time", "实时更新")}</span>
          </div>
        </div>
        <div className="system-health-stat running">
          <PlayCircle size={17} />
          <span>{l("Hồ sơ đang chạy", "Running profiles", "运行中的配置")}</span>
          <strong>
            {runningProfiles}/{data.profiles.length}
          </strong>
        </div>
        <div className={`system-health-stat ${resourceTone(ram)}`}>
          <MemoryStick size={17} />
          <span>RAM</span>
          <strong>{ram}%</strong>
          <i>
            <b style={{ width: `${Math.min(ram, 100)}%` }} />
          </i>
        </div>
        <div className={`system-health-stat ${resourceTone(cpu)}`}>
          <Cpu size={17} />
          <span>CPU</span>
          <strong>{cpu}%</strong>
          <i>
            <b style={{ width: `${Math.min(cpu, 100)}%` }} />
          </i>
        </div>
        {data.errors.length === 0 && (
          <div className="system-health-status">
            <CheckCircle2 size={18} />
            <div>
              <strong>{l("Hệ thống hoạt động bình thường", "The system is operating normally", "系统运行正常")}</strong>
              <span>{l("Không có lỗi nào cần xử lý", "No errors require attention", "没有需要处理的错误")}</span>
            </div>
          </div>
        )}
      </section>

      <div
        className={`overview-activity-line ${latestActivity?.status === "failed" || latestActivity?.status?.startsWith("failed") ? "failed" : ""}`}
      >
        <span className="activity-pulse" />
        <strong>{l("Hoạt động gần nhất", "Latest activity", "最近活动")}</strong>
        <span>{activityText(data, l)}</span>
        {latestActivity?.occurred_at && <time>{formatDateTime(latestActivity.occurred_at)}</time>}
      </div>

      {data.errors.length > 0 ? (
        <Section
          title={l(
            `Lỗi cần xử lý · ${data.errors.length}`,
            `Errors requiring attention · ${data.errors.length}`,
            `需要处理的错误 · ${data.errors.length}`,
          )}
        >
          <div className="overview-error-list">
            {data.errors.map((item) => (
              <button key={`${item.profile_id}:${item.video_id}`} onClick={() => showQueue("failed")}>
                <span>
                  <AlertCircle size={17} />
                </span>
                <div>
                  <strong>
                    {l(`Hồ sơ ${item.profile_id}`, `Profile ${item.profile_id}`, `配置 ${item.profile_id}`)}
                  </strong>
                  <p>{item.error || item.status}</p>
                </div>
                <time>{formatDateTime(item.updated_at)}</time>
              </button>
            ))}
          </div>
        </Section>
      ) : null}

      <div className="two-column overview-insights">
        <Section title={l("Hoạt động 7 ngày gần nhất", "Activity over the last 7 days", "最近 7 天的活动")}>
          <div className="daily-chart overview-daily-chart">
            {data.daily.map((day) => (
              <button
                className={`daily-column ${activeDay?.date === day.date ? "active" : ""}`}
                key={day.date}
                onClick={() => setSelectedDay(day.date)}
                title={`${day.date} · ${l("Thành công", "Successful", "成功")}: ${day.completed} · ${l("Bị hạn chế", "Restricted", "受限")}: ${day.restricted} · ${l("Lỗi đăng", "Publishing errors", "发布错误")}: ${day.publish_error}`}
              >
                <div className="bar-stack">
                  <i
                    className="bar success"
                    style={{ height: `${day.completed ? Math.max(10, (day.completed * 100) / maxDaily) : 3}%` }}
                  >
                    <b>{day.completed}</b>
                  </i>
                  <i
                    className="bar restricted"
                    style={{ height: `${day.restricted ? Math.max(10, (day.restricted * 100) / maxDaily) : 3}%` }}
                  >
                    <b>{day.restricted}</b>
                  </i>
                  <i
                    className="bar failed"
                    style={{ height: `${day.publish_error ? Math.max(10, (day.publish_error * 100) / maxDaily) : 3}%` }}
                  >
                    <b>{day.publish_error}</b>
                  </i>
                </div>
                <span>{day.label}</span>
              </button>
            ))}
          </div>
          {activeDay && (
            <div className="chart-day-detail">
              <div>
                <strong>
                  {activeDay.label} · {activeDay.date}
                </strong>
                <span>
                  {l("Đơn vị: video trên mọi nền tảng", "Unit: videos across all platforms", "单位：所有平台的视频")}
                </span>
              </div>
              <p>
                <span className="success">
                  <b>{activeDay.completed}</b>
                  {l("Thành công", "Successful", "成功")}
                </span>
                <span className="restricted">
                  <b>{activeDay.restricted}</b>
                  {l("Bị hạn chế", "Restricted", "受限")}
                </span>
                <span className="failed">
                  <b>{activeDay.publish_error}</b>
                  {l("Lỗi đăng", "Publishing errors", "发布错误")}
                </span>
              </p>
            </div>
          )}
          <div className="chart-legend">
            <span>
              <i className="success" />
              {l("Thành công", "Successful", "成功")}
            </span>
            <span>
              <i className="restricted" />
              {l("Bị hạn chế", "Restricted", "受限")}
            </span>
            <span>
              <i className="failed" />
              {l("Lỗi đăng", "Publishing errors", "发布错误")}
            </span>
            <small>
              {l(
                "Đơn vị: video · Bấm vào ngày để xem chi tiết",
                "Unit: videos · Click a day for details",
                "单位：视频 · 点击日期查看详情",
              )}
            </small>
          </div>
        </Section>

        <Section title={l("Hiệu suất nền tảng", "Platform performance", "平台表现")}>
          <div className="overview-platform-cards">
            {data.platforms.map((platform) => (
              <article className={`overview-platform-card ${platform.key}`} key={platform.key}>
                <div className="platform-card-heading">
                  <span>{platform.key === "youtube" ? "▶" : platform.key === "facebook" ? "f" : "♪"}</span>
                  <strong>{platformNames[platform.key]}</strong>
                  <b>{platform.rate}%</b>
                </div>
                <div className="platform-rate-track">
                  <i style={{ width: `${platform.rate}%` }} />
                </div>
                <div className="platform-card-stats">
                  <span>
                    <b>{platform.success}</b>
                    {l("Thành công", "Successful", "成功")}
                  </span>
                  <span>
                    <b>{platform.pending}</b>
                    {l("Đang chờ", "Pending", "等待")}
                  </span>
                  <span className={platform.failed ? "failed" : ""}>
                    <b>{platform.failed}</b>
                    {l("Thất bại", "Failed", "失败")}
                  </span>
                </div>
              </article>
            ))}
          </div>
        </Section>
      </div>

      <div ref={queueRef} className="overview-queue-anchor">
        <VideoQueueSection
          onChanged={overview.refresh}
          showFilters
          showReset={false}
          filter={queueFilter}
          onFilterChange={setQueueFilter}
          onViewAll={onOpenTracking}
        />
      </div>
    </div>
  );
}

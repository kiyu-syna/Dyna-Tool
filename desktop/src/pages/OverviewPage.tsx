import { AlertCircle, CheckCircle2, Clock3, Gauge, Radar, Video } from "lucide-react";
import { formatDateTime, shortId } from "../api";
import { EmptyState, Section, SkeletonRows, StatusPill } from "../components/Common";
import { usePolling } from "../hooks";
import type { Overview } from "../types";

const metrics = [
  ["detected_today", "Phát hiện hôm nay", Video, "blue"],
  ["completed_today", "Hoàn tất hôm nay", CheckCircle2, "green"],
  ["waiting", "Đang chờ", Clock3, "amber"],
  ["processing", "Đang xử lý", Radar, "cyan"],
  ["errors", "Cần xử lý", AlertCircle, "red"],
  ["success_rate", "Tỷ lệ thành công", Gauge, "violet"],
] as const;

function healthTone(health: string) {
  if (["healthy", "running"].includes(health)) return "success" as const;
  if (["disconnected", "unhealthy"].includes(health)) return "danger" as const;
  if (["recovering", "retrying"].includes(health)) return "warning" as const;
  return "neutral" as const;
}

function healthLabel(health: string) {
  return ({
    healthy: "Ổn định", checking: "Đang kiểm tra", recovering: "Đang phục hồi",
    retrying: "Đang thử lại", disconnected: "Mất kết nối", unhealthy: "Lỗi kết nối",
    starting: "Đang mở", unknown: "Chưa kiểm tra",
  } as Record<string, string>)[health] || health || "Chưa kiểm tra";
}

export default function OverviewPage() {
  const { data, error, loading } = usePolling<Overview>("/api/overview", 5000);
  if (loading && !data) return <SkeletonRows count={8} />;
  if (error && !data) return <EmptyState error message={error} />;
  if (!data) return null;
  const maxDaily = Math.max(1, ...data.daily.flatMap((day) => [day.completed, day.failed]));

  return (
    <div className="page-stack">
      <div className="metrics-grid">
        {metrics.map(([key, label, Icon, color]) => (
          <article className="metric-card" key={key}>
            <span className={`metric-icon ${color}`}><Icon size={18} /></span>
            <div><p>{label}</p><strong>{data.kpis[key] ?? 0}{key === "success_rate" ? "%" : ""}</strong></div>
          </article>
        ))}
      </div>

      <div className="two-column">
        <Section title="Hoạt động 7 ngày gần nhất">
          <div className="daily-chart">
            {data.daily.map((day) => (
              <div className="daily-column" key={day.date} title={day.date}>
                <div className="bar-stack">
                  <i className="bar success" style={{ height: `${Math.max(4, day.completed * 100 / maxDaily)}%` }} />
                  <i className="bar failed" style={{ height: `${Math.max(4, day.failed * 100 / maxDaily)}%` }} />
                </div>
                <span>{day.label}</span>
              </div>
            ))}
          </div>
          <div className="chart-legend"><span><i className="success" />Hoàn tất</span><span><i className="failed" />Lỗi</span></div>
        </Section>

        <Section title="Hiệu suất theo nền tảng">
          <div className="platform-list">
            {data.platforms.map((platform) => (
              <div className="platform-row" key={platform.key}>
                <div><strong>{platform.label}</strong><span>{platform.success} thành công · {platform.failed} lỗi · {platform.pending} chờ</span></div>
                <div className="rate"><strong>{platform.rate}%</strong><div><i style={{ width: `${platform.rate}%` }} /></div></div>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Section title="Tình trạng Profile">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Profile</th><th>Vận hành</th><th>Kết nối</th><th>Hàng đợi</th><th>Lỗi</th><th>Video hiện tại</th><th>Hoạt động cuối</th></tr></thead>
            <tbody>{data.profiles.map((profile) => (
              <tr key={profile.profile_id}>
                <td><div className="profile-cell"><b>{profile.profile_id}</b><span>{profile.name}</span></div></td>
                <td><StatusPill text={profile.running ? "Đang chạy" : "Đã dừng"} tone={profile.running ? "success" : "neutral"} /></td>
                <td><StatusPill text={healthLabel(profile.health)} tone={healthTone(profile.health)} /></td>
                <td>{profile.queue}</td><td className={profile.errors ? "danger-text" : ""}>{profile.errors}</td>
                <td className="mono">{shortId(profile.current_video)}</td><td>{formatDateTime(profile.last_activity)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>

      <Section title="Lỗi cần xử lý">
        {data.errors.length === 0 ? <EmptyState message="Không có lỗi nào cần xử lý" /> : (
          <div className="error-list">{data.errors.map((item) => (
            <div key={`${item.profile_id}:${item.video_id}`}>
              <AlertCircle size={16} /><strong>P{item.profile_id} · {shortId(item.video_id)}</strong>
              <span>{item.error || item.status}</span><time>{formatDateTime(item.updated_at)}</time>
            </div>
          ))}</div>
        )}
      </Section>
    </div>
  );
}

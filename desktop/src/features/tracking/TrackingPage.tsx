import { FlaskConical, Play, Square } from "lucide-react";
import { useState } from "react";
import { request } from "../../shared/api/client";
import { EmptyState, Modal, PlatformMarks, Section, SkeletonRows, StatusPill } from "../../shared/components/Common";
import VideoQueueSection from "../../shared/components/VideoQueueSection";
import { usePolling } from "../../shared/hooks/usePolling";
import { useI18n } from "../../shared/i18n";
import type {
  BrowserDiagnostic,
  BrowserDiagnosticHistory,
  ProfileSummary,
  RuntimeProfileState,
  RuntimeSnapshot,
  TestUploadState,
} from "../../shared/types";
import { SeenStateModal, TestUploadModal } from "./components/TrackingModals";
import "./tracking.css";

function runtimeTone(status: string) {
  if (status === "running") return "success" as const;
  if (status === "starting") return "info" as const;
  if (status === "degraded") return "warning" as const;
  if (status === "stopping") return "warning" as const;
  if (status === "error") return "danger" as const;
  return "neutral" as const;
}

function runtimeLabel(
  state: RuntimeProfileState | undefined,
  runningFallback: boolean,
  l: (vi: string, en: string, zh: string) => string,
) {
  if (!state) return runningFallback ? l("Đang xử lý", "Processing", "处理中") : l("Sẵn sàng", "Ready", "就绪");
  return (
    (
      {
        stopped: l("Sẵn sàng", "Ready", "就绪"),
        starting: l("Đang khởi động", "Starting", "正在启动"),
        running: l("Đang chạy", "Running", "运行中"),
        degraded: l("Có nguồn lỗi", "Degraded", "部分来源异常"),
        stopping: l("Đang dừng", "Stopping", "正在停止"),
        error: l("Lỗi", "Error", "错误"),
      } as Record<string, string>
    )[state.status] || state.status
  );
}

function resourceTone(percent: number | undefined) {
  const value = Number(percent || 0);
  if (value > 85) return "danger";
  if (value >= 70) return "warning";
  return "normal";
}

export default function TrackingPage() {
  const { l } = useI18n();
  const profiles = usePolling<{ profiles: ProfileSummary[] }>("/api/profiles", 5000);
  const runtime = usePolling<RuntimeSnapshot>("/api/runtime", 1500);
  const testUploads = usePolling<{ profiles: Record<string, TestUploadState> }>("/api/runtime/test-uploads", 2500);
  const diagnosticHistory = usePolling<BrowserDiagnosticHistory>("/api/diagnostics/history?limit=12", 5000);
  const [busyAction, setBusyAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [seenProfile, setSeenProfile] = useState<ProfileSummary | null>(null);
  const [testProfile, setTestProfile] = useState<ProfileSummary | null>(null);
  const [selectedDiagnostic, setSelectedDiagnostic] = useState<BrowserDiagnostic | null>(null);
  const [diagnosticImage, setDiagnosticImage] = useState("");
  const [diagnosticImageError, setDiagnosticImageError] = useState("");

  async function openDiagnostic(item: BrowserDiagnostic) {
    setSelectedDiagnostic(item);
    setDiagnosticImage("");
    setDiagnosticImageError("");
    if (!item.screenshot_available) return;
    try {
      const result = await request<{ data_url: string }>(
        `/api/diagnostics/history/${encodeURIComponent(item.event_id)}/screenshot`,
      );
      setDiagnosticImage(result.data_url);
    } catch (caught) {
      setDiagnosticImageError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function runtimeAction(path: string, actionKey: string) {
    setBusyAction(actionKey);
    setActionError("");
    try {
      await request(path, { method: "POST" });
      await Promise.all([runtime.refresh(), profiles.refresh()]);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function startTestUpload() {
    if (!testProfile) return;
    const profile = testProfile;
    setBusyAction(`test-${profile.id}`);
    setActionError("");
    try {
      await request(`/api/runtime/profiles/${encodeURIComponent(profile.id)}/test-upload`, {
        method: "POST",
        body: { confirmed: true },
      });
      setTestProfile(null);
      await testUploads.refresh();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }

  const activeCount = runtime.data?.active_profile_ids.length || 0;
  const startableCount =
    profiles.data?.profiles.filter((profile) => profile.enabled && profile.enabled_source_count > 0).length || 0;
  const systemResources = runtime.data?.resources?.system;
  const ramTone = resourceTone(systemResources?.ram_percent);
  const workload = runtime.data?.resources?.workload || {};
  const latestTestResult = Object.values(testUploads.data?.profiles || {})
    .filter((state) => !state.active && ["completed", "completed_with_errors", "failed"].includes(state.status))
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0];
  const workloadLabel = (key: string, label: string) => {
    const item = workload[key];
    return item
      ? `${label} ${item.active_count}/${item.limit}${item.waiting_count ? ` (+${item.waiting_count} ${l("chờ", "waiting", "等待")})` : ""}`
      : "";
  };

  return (
    <div className="page-stack">
      <Section
        title={l("Danh sách hồ sơ", "Profile list", "配置文件列表")}
        action={
          <div className="inline-actions">
            <span className="runtime-summary">
              {activeCount}/{startableCount} {l("đang chạy", "running", "运行中")}
            </span>
            {systemResources && (
              <>
                <span
                  className={`runtime-summary resource-summary resource-ram ${ramTone}`}
                  title={
                    ramTone === "danger"
                      ? l("RAM đang ở mức cao", "RAM usage is high", "内存占用过高")
                      : ramTone === "warning"
                        ? l("RAM cần theo dõi", "RAM usage needs attention", "内存占用需要关注")
                        : l("RAM ở mức bình thường", "RAM usage is normal", "内存占用正常")
                  }
                >
                  RAM {systemResources.ram_percent ?? 0}%
                </span>
                <span className="runtime-summary resource-summary">CPU {systemResources.cpu_percent ?? 0}%</span>
              </>
            )}
            <span className="runtime-summary resource-summary">
              {[
                workloadLabel("download", l("Tải", "Download", "下载")),
                workloadLabel("ffmpeg", "FFmpeg"),
                workloadLabel("upload", l("Đăng", "Publish", "发布")),
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
            <button
              className="small-button success"
              disabled={Boolean(busyAction) || startableCount === 0 || activeCount >= startableCount}
              onClick={() => void runtimeAction("/api/runtime/start-all", "start-all")}
              title={l("Bắt đầu tất cả hồ sơ đang bật", "Start all enabled Profiles", "启动所有已启用的配置文件")}
            >
              <Play size={14} />
              {l("Bắt đầu tất cả", "Start all", "全部启动")}
            </button>
            <button
              className="small-button danger"
              disabled={Boolean(busyAction) || activeCount === 0}
              onClick={() => void runtimeAction("/api/runtime/stop-all", "stop-all")}
              title={l(
                "Dừng và đóng cửa sổ của tất cả hồ sơ",
                "Stop and close all Profile windows",
                "停止并关闭所有配置文件窗口",
              )}
            >
              <Square size={13} />
              {l("Dừng tất cả", "Stop all", "全部停止")}
            </button>
          </div>
        }
      >
        {actionError && <div className="action-error">{actionError}</div>}
        {latestTestResult && (
          <div
            className={`test-upload-result ${latestTestResult.status === "completed" ? "success" : "warning"}`}
            role="status"
          >
            <strong>
              {l("Kết quả đăng thử", "Test-publish result", "测试发布结果")} — {latestTestResult.message}
            </strong>
            {latestTestResult.last_error && <span>{latestTestResult.last_error}</span>}
            {Object.entries(latestTestResult.results || {}).map(([platform, result]) => (
              <span key={platform}>
                {platform === "facebook" ? "Facebook Reels" : platform === "youtube" ? "YouTube Shorts" : "TikTok"}:{" "}
                {result?.ok ? l("Thành công", "Succeeded", "成功") : result?.message || l("Thất bại", "Failed", "失败")}
              </span>
            ))}
          </div>
        )}
        {profiles.loading && !profiles.data ? (
          <SkeletonRows />
        ) : profiles.error && !profiles.data ? (
          <EmptyState error message={profiles.error} />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{l("Hồ sơ", "Profile", "配置文件")}</th>
                  <th>{l("Nguồn / chu kỳ", "Sources / interval", "来源 / 周期")}</th>
                  <th>{l("Nền tảng đăng", "Publishing platforms", "发布平台")}</th>
                  <th>{l("Hàng đợi", "Queue", "队列")}</th>
                  <th>{l("Lỗi", "Errors", "错误")}</th>
                  <th>RAM</th>
                  <th>CPU</th>
                  <th>{l("Trạng thái", "Status", "状态")}</th>
                  <th>{l("Thao tác", "Actions", "操作")}</th>
                </tr>
              </thead>
              <tbody>
                {profiles.data?.profiles.map((profile) => {
                  const state = runtime.data?.profiles[profile.id];
                  const active = state?.active ?? profile.running;
                  const stopping = state?.status === "stopping";
                  const hasNoSources = !active && profile.enabled_source_count === 0;
                  const actionKey = `${active ? "stop" : "start"}-${profile.id}`;
                  const testState = testUploads.data?.profiles[profile.id];
                  return (
                    <tr key={profile.id}>
                      <td>
                        <div className="profile-cell">
                          <b>{profile.id}</b>
                          <span>{profile.name}</span>
                        </div>
                      </td>
                      <td>
                        {profile.enabled_source_count}/{profile.source_count} {l("nguồn", "sources", "个来源")} ·{" "}
                        {profile.source_platforms?.tiktok || 0} TikTok · {profile.source_platforms?.douyin || 0} Douyin
                      </td>
                      <td>
                        <PlatformMarks platforms={profile.platforms} />
                      </td>
                      <td>{profile.queue_count}</td>
                      <td className={profile.error_count ? "danger-text" : ""}>{profile.error_count}</td>
                      <td
                        className="resource-cell"
                        title={`${state?.resources?.process_count || 0} ${l("tiến trình trình duyệt", "browser processes", "个浏览器进程")}`}
                      >
                        {state?.resources?.process_count ? `${state.resources.ram_mb} MB` : "-"}
                      </td>
                      <td className="resource-cell">
                        {state?.resources?.process_count ? `${state.resources.cpu_percent}%` : "-"}
                      </td>
                      <td>
                        <div className="runtime-state-cell" title={state?.last_error || state?.message || ""}>
                          <StatusPill
                            text={runtimeLabel(state, profile.running, l)}
                            tone={runtimeTone(state?.status || (profile.running ? "running" : "stopped"))}
                          />
                          {Boolean(state?.failing_source_count) && (
                            <small>
                              {state?.failing_source_count}/{state?.source_count}{" "}
                              {l("nguồn lỗi", "sources failing", "个来源异常")}
                            </small>
                          )}
                          {state?.last_successful_scan_at && (
                            <small title={state.last_successful_scan_at}>
                              {l("Quét OK", "Last OK", "最近成功")}{" "}
                              {new Date(state.last_successful_scan_at).toLocaleTimeString()}
                            </small>
                          )}
                          {state?.current_source && <small>{state.current_source}</small>}
                        </div>
                      </td>
                      <td>
                        <div className="row-actions">
                          <button
                            className={`small-button ${active ? "danger" : "success"}`}
                            disabled={
                              Boolean(busyAction) ||
                              stopping ||
                              (!active && (!profile.enabled || profile.enabled_source_count === 0))
                            }
                            onClick={() =>
                              void runtimeAction(
                                `/api/runtime/profiles/${encodeURIComponent(profile.id)}/${active ? "stop" : "start"}`,
                                actionKey,
                              )
                            }
                            title={
                              active
                                ? l(
                                    "Dừng và đóng cửa sổ hồ sơ",
                                    "Stop and close the Profile window",
                                    "停止并关闭配置文件窗口",
                                  )
                                : !profile.enabled
                                  ? l(
                                      "Hồ sơ đang bị tắt trong cấu hình",
                                      "Profile is disabled in settings",
                                      "配置文件已在设置中禁用",
                                    )
                                  : profile.enabled_source_count === 0
                                    ? l(
                                        "Hồ sơ chưa có nguồn theo dõi đang bật",
                                        "Profile has no enabled tracking source",
                                        "配置文件没有已启用的跟踪来源",
                                      )
                                    : l("Bắt đầu theo dõi hồ sơ", "Start Profile tracking", "开始跟踪配置文件")
                            }
                          >
                            {active ? <Square size={13} /> : <Play size={14} />}
                            {active
                              ? l("Dừng", "Stop", "停止")
                              : hasNoSources
                                ? l("Chưa có nguồn", "No source", "无来源")
                                : state?.status === "error"
                                  ? l("Thử lại", "Retry", "重试")
                                  : l("Bắt đầu", "Start", "启动")}
                          </button>
                          <button
                            className={`icon-button ${testState?.active ? "active" : ""}`}
                            disabled={active || Boolean(busyAction) || testState?.active}
                            onClick={() => setTestProfile(profile)}
                            title={
                              active
                                ? l(
                                    "Dừng hồ sơ trước khi đăng thử",
                                    "Stop the Profile before test publishing",
                                    "测试发布前请停止配置文件",
                                  )
                                : testState?.message ||
                                  l("Đăng thử video đầu tiên", "Test-publish the first video", "测试发布第一个视频")
                            }
                          >
                            <FlaskConical size={15} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        title={l("Lịch sử lỗi tự động", "Automatic error history", "自动错误历史")}
        action={
          <span className="runtime-summary">
            {diagnosticHistory.data?.items?.length || 0} {l("lỗi gần nhất", "recent errors", "条最近错误")}
          </span>
        }
      >
        {diagnosticHistory.error && !diagnosticHistory.data ? (
          <EmptyState error message={diagnosticHistory.error} />
        ) : !diagnosticHistory.data?.items?.length ? (
          <EmptyState message={l("Chưa có ảnh lỗi nào.", "No error screenshots yet.", "暂无错误截图。")} />
        ) : (
          <div className="diagnostic-history-grid">
            {diagnosticHistory.data.items.map((item) => (
              <button
                type="button"
                className="diagnostic-history-card"
                key={item.event_id}
                onClick={() => void openDiagnostic(item)}
              >
                <div>
                  <strong>{item.platform.toUpperCase()}</strong>
                  <time>{new Date(item.occurred_at).toLocaleString()}</time>
                </div>
                <span>
                  Profile {item.profile_id || "-"} · Video {item.video_id || "-"}
                </span>
                <p>{item.error || l("Lỗi không xác định", "Unknown error", "未知错误")}</p>
                <small>
                  {item.screenshot_available
                    ? l("Bấm để xem ảnh", "Click to view screenshot", "点击查看截图")
                    : item.screenshot_error || l("Không có ảnh", "No screenshot", "无截图")}
                </small>
              </button>
            ))}
          </div>
        )}
      </Section>

      <VideoQueueSection onChanged={profiles.refresh} showFilters />
      {seenProfile && (
        <SeenStateModal
          profile={seenProfile}
          active={runtime.data?.profiles[seenProfile.id]?.active ?? seenProfile.running}
          onClose={() => setSeenProfile(null)}
        />
      )}
      {testProfile && (
        <TestUploadModal
          profile={testProfile}
          submitting={busyAction === `test-${testProfile.id}`}
          onClose={() => setTestProfile(null)}
          onConfirm={() => void startTestUpload()}
        />
      )}
      {selectedDiagnostic && (
        <Modal
          title={`${selectedDiagnostic.platform.toUpperCase()} · Profile ${selectedDiagnostic.profile_id || "-"}`}
          onClose={() => setSelectedDiagnostic(null)}
          className="diagnostic-image-modal"
        >
          <div className="diagnostic-image-detail">
            <p>{selectedDiagnostic.error}</p>
            {diagnosticImageError ? (
              <div className="action-error">{diagnosticImageError}</div>
            ) : diagnosticImage ? (
              <img
                src={diagnosticImage}
                alt={l("Ảnh màn hình lúc xảy ra lỗi", "Screenshot at failure", "错误发生时的截图")}
              />
            ) : selectedDiagnostic.screenshot_available ? (
              <span>{l("Đang tải ảnh...", "Loading screenshot...", "正在加载截图...")}</span>
            ) : (
              <span>
                {l(
                  "Không chụp được ảnh cho lỗi này.",
                  "No screenshot was captured for this error.",
                  "此错误未能截取屏幕。",
                )}
              </span>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

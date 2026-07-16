import { AlertTriangle, Ban, Database, FlaskConical, Play, RefreshCw, RotateCcw, Save, Square, Stethoscope } from "lucide-react";
import { useEffect, useState } from "react";
import { formatDateTime, request, shortId } from "../api";
import { EmptyState, Modal, PlatformMarks, Section, SkeletonRows, StatusPill } from "../components/Common";
import { usePolling } from "../hooks";
import type { DiagnosticsResult, Job, ProfileSummary, RuntimeProfileState, RuntimeSnapshot, SeenSource, TestUploadState } from "../types";

function jobTone(status: string) {
  if (status === "completed") return "success" as const;
  if (status.startsWith("failed")) return "danger" as const;
  if (["uploading", "downloading"].includes(status)) return "info" as const;
  if (status === "cancelled") return "neutral" as const;
  return "warning" as const;
}

function jobLabel(status: string) {
  return ({
    detected: "Mới phát hiện", waiting_caption: "Chờ mô tả", caption_ready: "Đã có mô tả",
    downloading: "Đang tải", downloaded: "Đã tải", uploading: "Đang đăng",
    completed: "Hoàn tất", cancelled: "Đã hủy", ignored: "Đã bỏ qua",
    failed: "Lỗi", failed_download: "Lỗi tải", failed_upload: "Lỗi đăng",
  } as Record<string, string>)[status] || status;
}

function runtimeTone(status: string) {
  if (status === "running") return "success" as const;
  if (status === "starting") return "info" as const;
  if (status === "stopping") return "warning" as const;
  if (status === "error") return "danger" as const;
  return "neutral" as const;
}

function runtimeLabel(state?: RuntimeProfileState, runningFallback = false) {
  if (!state) return runningFallback ? "Đang xử lý" : "Sẵn sàng";
  return ({
    stopped: "Sẵn sàng",
    starting: "Đang khởi động",
    running: "Đang chạy",
    stopping: "Đang dừng",
    error: "Lỗi",
  } as Record<string, string>)[state.status] || state.status;
}

function SeenStateModal({ profile, active, onClose }: {
  profile: ProfileSummary;
  active: boolean;
  onClose(): void;
}) {
  const [sources, setSources] = useState<SeenSource[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void request<{ sources: SeenSource[] }>(`/api/profiles/${encodeURIComponent(profile.id)}/seen`)
      .then((result) => {
        setSources(result.sources);
        setSelectedKey(result.sources[0]?.source_key || "");
        if (!result.sources.length) setLoading(false);
      })
      .catch((caught) => {
        setError(caught instanceof Error ? caught.message : String(caught));
        setLoading(false);
      });
  }, [profile.id]);

  useEffect(() => {
    if (!selectedKey) return;
    setLoading(true);
    setMessage("");
    setError("");
    void request<{ data: Record<string, unknown> }>(`/api/profiles/${encodeURIComponent(profile.id)}/seen/${encodeURIComponent(selectedKey)}`)
      .then((result) => setContent(JSON.stringify(result.data, null, 2)))
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setLoading(false));
  }, [profile.id, selectedKey]);

  async function save() {
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const data = JSON.parse(content) as Record<string, unknown>;
      const result = await request<{ data: Record<string, unknown> }>(`/api/profiles/${encodeURIComponent(profile.id)}/seen/${encodeURIComponent(selectedKey)}`, {
        method: "PUT",
        body: { data },
      });
      setContent(JSON.stringify(result.data, null, 2));
      setMessage("Đã lưu dữ liệu đối chiếu");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  return <Modal title={`Dữ liệu đối chiếu · Profile ${profile.id}`} onClose={onClose} className="wide" footer={<>
    <span className={`modal-status ${error ? "danger-text" : "success-text"}`}>{error || message}</span>
    <button className="secondary-button" onClick={onClose}>Đóng</button>
    <button className="primary-button" disabled={active || saving || loading || !selectedKey} onClick={() => void save()} title={active ? "Dừng Profile trước khi chỉnh sửa" : "Lưu dữ liệu đối chiếu"}><Save size={15} />{saving ? "Đang lưu" : "Lưu"}</button>
  </>}>
    {!sources.length && !loading ? <EmptyState message="Profile chưa có nguồn Douyin để tạo dữ liệu đối chiếu" /> : <div className="seen-editor">
      <div className="seen-toolbar"><label><span>Nguồn Douyin</span><select value={selectedKey} onChange={(event) => setSelectedKey(event.target.value)}>{sources.map((source) => <option key={source.source_key} value={source.source_key}>{source.label} · {source.exists ? `${source.seen_count} video` : "chưa baseline"}</option>)}</select></label>{active && <StatusPill text="Đang chạy · chỉ đọc" tone="warning" />}</div>
      <textarea className="json-editor" spellCheck={false} value={content} onChange={(event) => setContent(event.target.value)} disabled={loading || active} aria-label="Dữ liệu JSON đối chiếu" />
    </div>}
  </Modal>;
}

function DiagnosticsModal({ result, onClose }: { result: DiagnosticsResult; onClose(): void }) {
  return <Modal title="Kết quả chẩn đoán" onClose={onClose} className="wide" footer={<button className="secondary-button" onClick={onClose}>Đóng</button>}>
    <div className="diagnostic-summary">
      <div><span>GemLogin API</span><strong className="mono">{result.api_url}</strong></div>
      <div><span>ffprobe</span><StatusPill text={result.ffprobe.ok ? "Sẵn sàng" : "Chưa sẵn sàng"} tone={result.ffprobe.ok ? "success" : "danger"} /></div>
      <div><span>Telegram</span><StatusPill text={result.telegram.ok ? "Đã cấu hình" : "Thiếu cấu hình"} tone={result.telegram.ok ? "success" : "warning"} /></div>
    </div>
    <div className="table-wrap diagnostic-table"><table><thead><tr><th>Profile</th><th>GemLogin</th><th>Nguồn</th><th>Nền tảng</th><th>Kết nối</th><th>Chi tiết</th></tr></thead><tbody>{result.profiles.map((item) => <tr key={item.profile_id}>
      <td><div className="profile-cell"><b>{item.profile_id}</b><span>{item.name}</span></div></td><td className="mono">{item.gemlogin_profile_id}</td><td>{item.source_count}</td><td>{item.platforms.join(", ") || "-"}</td><td><StatusPill text={item.ok ? "Sẵn sàng" : "Cần kiểm tra"} tone={item.ok ? "success" : "danger"} /></td><td className="diagnostic-message" title={item.message}>{item.message}</td>
    </tr>)}</tbody></table></div>
  </Modal>;
}

function TestUploadModal({ profile, onClose, onConfirm, submitting }: {
  profile: ProfileSummary;
  onClose(): void;
  onConfirm(): void;
  submitting: boolean;
}) {
  const enabledPlatforms = Object.entries(profile.platforms).filter(([, enabled]) => enabled).map(([key]) => key);
  return <Modal title={`Test đăng video · Profile ${profile.id}`} onClose={onClose} footer={<>
    <button className="secondary-button" onClick={onClose} disabled={submitting}>Hủy bỏ</button>
    <button className="small-button destructive" onClick={onConfirm} disabled={submitting}><FlaskConical size={15} />{submitting ? "Đang bắt đầu" : "Xác nhận test đăng thật"}</button>
  </>}>
    <div className="test-upload-warning"><AlertTriangle size={23} /><div><strong>Thao tác này sẽ đăng video thật.</strong><p>Hệ thống lấy video đầu tiên từ nguồn Douyin đang bật, yêu cầu mô tả theo cấu hình rồi đăng lên: {enabledPlatforms.join(", ") || "không có nền tảng"}. Dữ liệu baseline không bị thay đổi.</p></div></div>
  </Modal>;
}

export default function TrackingPage() {
  const profiles = usePolling<{ profiles: ProfileSummary[] }>("/api/profiles", 5000);
  const jobs = usePolling<{ jobs: Job[]; total: number }>("/api/jobs?limit=100", 3000);
  const runtime = usePolling<RuntimeSnapshot>("/api/runtime", 1500);
  const testUploads = usePolling<{ profiles: Record<string, TestUploadState> }>("/api/runtime/test-uploads", 2500);
  const [busyAction, setBusyAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [seenProfile, setSeenProfile] = useState<ProfileSummary | null>(null);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsResult | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const [testProfile, setTestProfile] = useState<ProfileSummary | null>(null);

  async function act(action: "retry" | "cancel", job: Job) {
    await request(`/api/jobs/${action}`, { method: "POST", body: { profile_id: job.profile_id, video_id: job.video_id } });
    await jobs.refresh();
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

  async function runDiagnostics() {
    setDiagnosing(true);
    setActionError("");
    try {
      const result = await request<DiagnosticsResult>("/api/diagnostics", {
        method: "POST",
        body: { profile_id: null },
      });
      setDiagnostics(result);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setDiagnosing(false);
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
  const startableCount = profiles.data?.profiles.filter(
    (profile) => profile.enabled && profile.enabled_source_count > 0,
  ).length || 0;
  const systemResources = runtime.data?.resources?.system;
  const workload = runtime.data?.resources?.workload || {};
  const workloadLabel = (key: string, label: string) => {
    const item = workload[key];
    return item ? `${label} ${item.active_count}/${item.limit}${item.waiting_count ? ` (+${item.waiting_count} chờ)` : ""}` : "";
  };

  return (
    <div className="page-stack">
      <Section title="Danh sách Profile" action={
        <div className="inline-actions">
          <span className="runtime-summary">{activeCount}/{startableCount} đang chạy</span>
          {systemResources && <span className="runtime-summary resource-summary">RAM {systemResources.ram_percent ?? 0}% · CPU {systemResources.cpu_percent ?? 0}%</span>}
          <span className="runtime-summary resource-summary">{[workloadLabel("download", "Tải"), workloadLabel("ffmpeg", "FFmpeg"), workloadLabel("upload", "Upload")].filter(Boolean).join(" · ")}</span>
          <button className="small-button" disabled={diagnosing || Boolean(busyAction) || activeCount > 0} onClick={() => void runDiagnostics()} title={activeCount > 0 ? "Dừng tất cả Profile trước khi chẩn đoán" : "Kiểm tra ffprobe, Telegram và GemLogin/CDP"}><Stethoscope size={14} />{diagnosing ? "Đang kiểm tra" : "Chẩn đoán"}</button>
          <button
            className="small-button success"
            disabled={Boolean(busyAction) || startableCount === 0 || activeCount >= startableCount}
            onClick={() => void runtimeAction("/api/runtime/start-all", "start-all")}
            title="Bắt đầu tất cả Profile đang bật"
          ><Play size={14} />Bắt đầu tất cả</button>
          <button
            className="small-button danger"
            disabled={Boolean(busyAction) || activeCount === 0}
            onClick={() => void runtimeAction("/api/runtime/stop-all", "stop-all")}
            title="Dừng và đóng cửa sổ của tất cả Profile"
          ><Square size={13} />Dừng tất cả</button>
          <button className="icon-button" onClick={() => void Promise.all([runtime.refresh(), profiles.refresh()])} title="Tải lại"><RefreshCw size={16} /></button>
        </div>
      }>
        {actionError && <div className="action-error">{actionError}</div>}
        {profiles.loading && !profiles.data ? <SkeletonRows /> : profiles.error && !profiles.data ? <EmptyState error message={profiles.error} /> : (
          <div className="table-wrap">
            <table><thead><tr><th>Profile</th><th>Nguồn / chu kỳ</th><th>Nền tảng đăng</th><th>Hàng đợi</th><th>Lỗi</th><th>RAM</th><th>CPU</th><th>Trạng thái</th><th>Thao tác</th></tr></thead>
              <tbody>{profiles.data?.profiles.map((profile) => {
                const state = runtime.data?.profiles[profile.id];
                const active = state?.active ?? profile.running;
                const stopping = state?.status === "stopping";
                const actionKey = `${active ? "stop" : "start"}-${profile.id}`;
                const testState = testUploads.data?.profiles[profile.id];
                return (
                  <tr key={profile.id}>
                    <td><div className="profile-cell"><b>{profile.id}</b><span>{profile.name}</span></div></td>
                    <td>{profile.enabled_source_count}/{profile.source_count} nguồn · {profile.check_interval_minutes}p</td>
                    <td><PlatformMarks platforms={profile.platforms} /></td><td>{profile.queue_count}</td>
                    <td className={profile.error_count ? "danger-text" : ""}>{profile.error_count}</td>
                    <td className="resource-cell" title={`${state?.resources?.process_count || 0} tiến trình browser`}>{state?.resources?.process_count ? `${state.resources.ram_mb} MB` : "-"}</td>
                    <td className="resource-cell">{state?.resources?.process_count ? `${state.resources.cpu_percent}%` : "-"}</td>
                    <td>
                      <div className="runtime-state-cell" title={state?.last_error || state?.message || ""}>
                        <StatusPill text={runtimeLabel(state, profile.running)} tone={runtimeTone(state?.status || (profile.running ? "running" : "stopped"))} />
                        {state?.current_source && <small>{state.current_source}</small>}
                      </div>
                    </td>
                    <td>
                      <div className="row-actions"><button
                        className={`small-button ${active ? "danger" : "success"}`}
                        disabled={Boolean(busyAction) || stopping || (!active && (!profile.enabled || profile.enabled_source_count === 0))}
                        onClick={() => void runtimeAction(`/api/runtime/profiles/${encodeURIComponent(profile.id)}/${active ? "stop" : "start"}`, actionKey)}
                        title={active ? "Dừng và đóng cửa sổ Profile" : !profile.enabled ? "Profile đang bị tắt trong cấu hình" : profile.enabled_source_count === 0 ? "Profile chưa có nguồn Douyin đang bật" : "Bắt đầu Tracking Profile"}
                      >{active ? <Square size={13} /> : <Play size={14} />}{active ? "Dừng" : state?.status === "error" ? "Thử lại" : "Bắt đầu"}</button>
                      <button className={`icon-button ${testState?.active ? "active" : ""}`} disabled={active || Boolean(busyAction) || testState?.active} onClick={() => setTestProfile(profile)} title={active ? "Dừng Profile trước khi test" : testState?.message || "Test đăng thật video đầu tiên"}><FlaskConical size={15} /></button>
                      <button className="icon-button" onClick={() => setSeenProfile(profile)} title="Dữ liệu đối chiếu"><Database size={15} /></button></div>
                    </td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title={`Hàng đợi video${jobs.data ? ` · ${jobs.data.total}` : ""}`} action={<button className="icon-button" onClick={jobs.refresh} title="Tải lại"><RefreshCw size={16} /></button>}>
        {jobs.loading && !jobs.data ? <SkeletonRows /> : jobs.error && !jobs.data ? <EmptyState error message={jobs.error} /> : !jobs.data?.jobs.length ? <EmptyState message="Chưa có video trong hàng đợi" /> : (
          <div className="table-wrap queue-table"><table><thead><tr><th>Profile</th><th>Video</th><th>Nguồn</th><th>Nền tảng</th><th>Trạng thái</th><th>Cập nhật</th><th>Thao tác</th></tr></thead>
            <tbody>{jobs.data.jobs.map((job) => (
              <tr key={`${job.profile_id}:${job.video_id}`}>
                <td>P{job.profile_id}</td><td className="mono" title={job.video_id}>{shortId(job.video_id, 14)}</td><td>{job.source_label || "-"}</td>
                <td><PlatformMarks platforms={job.enabled_platforms || []} /></td><td><StatusPill text={jobLabel(job.status)} tone={jobTone(job.status)} /></td>
                <td>{formatDateTime(job.updated_at)}</td><td><div className="row-actions">
                  {job.status.startsWith("failed") && <button className="small-button" onClick={() => void act("retry", job)}><RotateCcw size={14} />Thử lại</button>}
                  {!(["completed", "cancelled", "ignored"].includes(job.status)) && <button className="small-button danger" onClick={() => void act("cancel", job)}><Ban size={14} />Hủy</button>}
                </div></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Section>
      {seenProfile && <SeenStateModal profile={seenProfile} active={runtime.data?.profiles[seenProfile.id]?.active ?? seenProfile.running} onClose={() => setSeenProfile(null)} />}
      {diagnostics && <DiagnosticsModal result={diagnostics} onClose={() => setDiagnostics(null)} />}
      {testProfile && <TestUploadModal profile={testProfile} submitting={busyAction === `test-${testProfile.id}`} onClose={() => setTestProfile(null)} onConfirm={() => void startTestUpload()} />}
    </div>
  );
}

import { AlertTriangle, FlaskConical, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../../../shared/api/client";
import { EmptyState, Modal, StatusPill } from "../../../shared/components/Common";
import { useI18n } from "../../../shared/i18n";
import type { ProfileSummary, SeenSource } from "../../../shared/types";

export function SeenStateModal({
  profile,
  active,
  onClose,
}: {
  profile: ProfileSummary;
  active: boolean;
  onClose(): void;
}) {
  const { l } = useI18n();
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
    void request<{ data: Record<string, unknown> }>(
      `/api/profiles/${encodeURIComponent(profile.id)}/seen/${encodeURIComponent(selectedKey)}`,
    )
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
      const result = await request<{ data: Record<string, unknown> }>(
        `/api/profiles/${encodeURIComponent(profile.id)}/seen/${encodeURIComponent(selectedKey)}`,
        {
          method: "PUT",
          body: { data },
        },
      );
      setContent(JSON.stringify(result.data, null, 2));
      setMessage(l("Đã lưu dữ liệu đối chiếu", "Reconciliation data saved", "核对数据已保存"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={`${l("Dữ liệu đối chiếu", "Reconciliation data", "核对数据")} · ${l("Hồ sơ", "Profile", "配置文件")} ${profile.id}`}
      onClose={onClose}
      className="wide"
      footer={
        <>
          <span className={`modal-status ${error ? "danger-text" : "success-text"}`}>{error || message}</span>
          <button className="secondary-button" onClick={onClose}>
            {l("Đóng", "Close", "关闭")}
          </button>
          <button
            className="primary-button"
            disabled={active || saving || loading || !selectedKey}
            onClick={() => void save()}
            title={
              active
                ? l("Dừng hồ sơ trước khi chỉnh sửa", "Stop the Profile before editing", "编辑前请停止配置文件")
                : l("Lưu dữ liệu đối chiếu", "Save reconciliation data", "保存核对数据")
            }
          >
            <Save size={15} />
            {saving ? l("Đang lưu", "Saving", "正在保存") : l("Lưu", "Save", "保存")}
          </button>
        </>
      }
    >
      {!sources.length && !loading ? (
        <EmptyState
          message={l(
            "Hồ sơ chưa có nguồn theo dõi để tạo dữ liệu đối chiếu",
            "The Profile has no tracking source for reconciliation data",
            "该配置文件没有可用于核对数据的跟踪来源",
          )}
        />
      ) : (
        <div className="seen-editor">
          <div className="seen-toolbar">
            <label>
              <span>{l("Nguồn theo dõi", "Tracking source", "跟踪来源")}</span>
              <select value={selectedKey} onChange={(event) => setSelectedKey(event.target.value)}>
                {sources.map((source) => (
                  <option key={source.source_key} value={source.source_key}>
                    {source.platform ? `${source.platform === "tiktok" ? "TikTok" : "Douyin"} · ` : ""}
                    {source.label} ·{" "}
                    {source.exists
                      ? l(`${source.seen_count} video`, `${source.seen_count} videos`, `${source.seen_count} 个视频`)
                      : l("chưa có dữ liệu gốc", "no baseline", "暂无基准数据")}
                  </option>
                ))}
              </select>
            </label>
            {active && (
              <StatusPill text={l("Đang chạy · chỉ đọc", "Running · read only", "运行中 · 只读")} tone="warning" />
            )}
          </div>
          <textarea
            className="json-editor"
            spellCheck={false}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            disabled={loading || active}
            aria-label={l("Dữ liệu JSON đối chiếu", "Reconciliation JSON data", "核对 JSON 数据")}
          />
        </div>
      )}
    </Modal>
  );
}

export function TestUploadModal({
  profile,
  onClose,
  onConfirm,
  submitting,
}: {
  profile: ProfileSummary;
  onClose(): void;
  onConfirm(): void;
  submitting: boolean;
}) {
  const { l } = useI18n();
  const enabledPlatforms = Object.entries(profile.platforms)
    .filter(([, enabled]) => enabled)
    .map(([key]) => key);
  return (
    <Modal
      title={`${l("Đăng thử video", "Test video publishing", "测试视频发布")} · ${l("Hồ sơ", "Profile", "配置文件")} ${profile.id}`}
      onClose={onClose}
      footer={
        <>
          <button className="secondary-button" onClick={onClose} disabled={submitting}>
            {l("Hủy bỏ", "Cancel", "取消")}
          </button>
          <button className="small-button destructive" onClick={onConfirm} disabled={submitting}>
            <FlaskConical size={15} />
            {submitting
              ? l("Đang bắt đầu", "Starting", "正在启动")
              : l("Xác nhận đăng thử thật", "Confirm real test publish", "确认实际测试发布")}
          </button>
        </>
      }
    >
      <div className="test-upload-warning">
        <AlertTriangle size={23} />
        <div>
          <strong>
            {l("Thao tác này sẽ đăng video thật.", "This action publishes a real video.", "此操作将发布真实视频。")}
          </strong>
          <p>
            {l(
              "Hệ thống lấy video đầu tiên từ nguồn theo dõi đang bật, tạo mô tả theo cấu hình rồi đăng lên",
              "The system takes the first video from an enabled tracking source, prepares its caption, and publishes it to",
              "系统会从已启用的跟踪来源获取第一个视频，按设置生成文案并发布到",
            )}
            : {enabledPlatforms.join(", ") || l("không có nền tảng", "no platform", "无平台")}
            {l(".", ".", "。")}{" "}
            {l("Dữ liệu đối chiếu không bị thay đổi.", "Reconciliation data is not changed.", "核对数据不会被修改。")}
          </p>
        </div>
      </div>
    </Modal>
  );
}

import { Link2, Save, Unlink } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../../shared/api/client";
import { EmptyState, SkeletonRows } from "../../shared/components/Common";
import type { AppSettings } from "../../shared/types";
import { useI18n, type Language } from "../../shared/i18n";
import "./settings.css";

type TelegramNotificationType = "new_video" | "upload_success" | "upload_failure" | "high_ram" | "job_confirmation";

export default function SettingsPage({
  theme,
  language,
  onTheme,
  onLanguage,
}: {
  theme: "system" | "light" | "dark";
  language: Language;
  onTheme(value: "system" | "light" | "dark"): void;
  onLanguage(value: Language): void;
}) {
  const { l } = useI18n();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [telegram, setTelegram] = useState<{
    configured: boolean;
    linked: boolean;
    chat_name?: string;
    bot_username?: string;
  } | null>(null);
  const [linkCode, setLinkCode] = useState("");
  const [linking, setLinking] = useState(false);
  const telegramTarget = telegram?.bot_username ? `@${telegram.bot_username}` : "bot Dyna";
  useEffect(() => {
    void request<{ settings: AppSettings }>("/api/settings")
      .then((result) => setSettings(result.settings))
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)));
  }, []);
  useEffect(() => {
    void request<{ configured: boolean; linked: boolean; chat_name?: string; bot_username?: string }>(
      "/api/telegram/status",
    )
      .then(setTelegram)
      .catch(() => setTelegram({ configured: false, linked: false }));
  }, []);

  function update(next: Partial<AppSettings>) {
    setSettings((current) => ({ ...(current || {}), ...next }));
    setSaved(false);
  }

  async function save() {
    if (!settings) return;
    setSaving(true);
    try {
      const next = { ...settings, UI_THEME: theme, UI_LANGUAGE: language };
      const result = await request<{ settings: AppSettings }>("/api/settings", {
        method: "PUT",
        body: { settings: next },
      });
      setSettings(result.settings);
      setSaved(true);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  function updateNotificationPreference(type: TelegramNotificationType, enabled: boolean) {
    update({ TELEGRAM_NOTIFICATION_TYPES: { ...settings?.TELEGRAM_NOTIFICATION_TYPES, [type]: enabled } });
  }

  async function createLinkCode() {
    setLinking(true);
    try {
      const result = await request<{ code: string; bot_username?: string }>("/api/telegram/link-code", {
        method: "POST",
      });
      setLinkCode(result.code);
      setTelegram((current) => ({
        ...(current || { configured: true, linked: false }),
        bot_username: result.bot_username || current?.bot_username,
      }));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLinking(false);
    }
  }

  async function unlinkTelegram() {
    setLinking(true);
    try {
      await request("/api/telegram/link", { method: "DELETE" });
      setTelegram((current) => ({ ...(current || { configured: true }), linked: false, chat_name: "" }));
      setLinkCode("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLinking(false);
    }
  }

  if (!settings && !error) return <SkeletonRows count={8} />;
  if (!settings) return <EmptyState error message={error} />;
  return (
    <div className="settings-page">
      <section className="form-section">
        <h3>{l("Giao diện", "Appearance", "界面")}</h3>
        <div className="settings-row">
          <div>
            <strong>{l("Chủ đề", "Theme", "主题")}</strong>
            <span>{l("Màu sắc giao diện ứng dụng", "Application color theme", "应用界面配色")}</span>
          </div>
          <div className="segmented">
            <button className={theme === "system" ? "selected" : ""} onClick={() => onTheme("system")}>
              {l("Hệ thống", "System", "系统")}
            </button>
            <button className={theme === "light" ? "selected" : ""} onClick={() => onTheme("light")}>
              {l("Sáng", "Light", "浅色")}
            </button>
            <button className={theme === "dark" ? "selected" : ""} onClick={() => onTheme("dark")}>
              {l("Tối", "Dark", "深色")}
            </button>
          </div>
        </div>
        <div className="settings-row">
          <div>
            <strong>{l("Ngôn ngữ", "Language", "语言")}</strong>
            <span>{l("Ngôn ngữ hiển thị mặc định", "Default display language", "默认显示语言")}</span>
          </div>
          <div className="segmented">
            <button className={language === "vi" ? "selected" : ""} onClick={() => onLanguage("vi")}>
              Tiếng Việt
            </button>
            <button className={language === "en" ? "selected" : ""} onClick={() => onLanguage("en")}>
              English
            </button>
            <button className={language === "zh" ? "selected" : ""} onClick={() => onLanguage("zh")}>
              中文简体
            </button>
            <button className={language === "zh-TW" ? "selected" : ""} onClick={() => onLanguage("zh-TW")}>
              中文繁體
            </button>
          </div>
        </div>
      </section>
      <section className="form-section">
        <h3>{l("Liên kết Telegram", "Link Telegram", "关联 Telegram")}</h3>
        {!telegram?.configured ? (
          <p className="danger-text">
            {l(
              "Bot Telegram chưa được cấu hình trên máy chủ Dyna.",
              "The Telegram bot has not been configured on the Dyna server.",
              "Dyna 服务器尚未配置 Telegram 机器人。",
            )}
          </p>
        ) : telegram.linked ? (
          <div className="settings-row">
            <div>
              <strong>
                {l("Đã liên kết", "Connected", "已关联")}
                {telegram.chat_name ? ` · ${telegram.chat_name}` : ""}
              </strong>
              <span>
                {l(
                  "Thông báo từ Dyna sẽ gửi qua bot chung. Không có Bot Token trên máy này.",
                  "Dyna notifications use the shared bot. No bot token is stored on this device.",
                  "Dyna 通知通过共享机器人发送；此设备不存储机器人令牌。",
                )}
              </span>
            </div>
            <button className="secondary-button" disabled={linking} onClick={() => void unlinkTelegram()}>
              <Unlink size={16} />
              {l("Ngắt liên kết", "Unlink", "解除关联")}
            </button>
          </div>
        ) : (
          <div className="settings-row">
            <div>
              <strong>{l("Chưa liên kết Telegram", "Telegram not linked", "Telegram 未关联")}</strong>
              <span>
                {linkCode
                  ? l(
                      `Mở ${telegramTarget} và gửi: /start ${linkCode}`,
                      `Open ${telegramTarget} and send: /start ${linkCode}`,
                      `打开 ${telegramTarget} 并发送：/start ${linkCode}`,
                    )
                  : l(
                      "Tạo mã một lần rồi nhắn cho bot Dyna để liên kết an toàn.",
                      "Create a one-time code, then message the Dyna bot to link securely.",
                      "创建一次性代码，然后发送给 Dyna 机器人以安全关联。",
                    )}
              </span>
            </div>
            <button className="primary-button" disabled={linking} onClick={() => void createLinkCode()}>
              <Link2 size={16} />
              {linking
                ? l("Đang tạo", "Creating", "正在创建")
                : l("Liên kết Telegram", "Link Telegram", "关联 Telegram")}
            </button>
          </div>
        )}
        <div className="notification-preferences">
          <strong>{l("Loại thông báo muốn nhận", "Notification types", "通知类型")}</strong>
          <span>
            {l(
              "Chọn những thông báo Dyna gửi qua Telegram.",
              "Choose which Dyna notifications are sent to Telegram.",
              "选择 Dyna 发送到 Telegram 的通知。",
            )}
          </span>
          <div>
            {(
              [
                ["new_video", l("Video mới", "New video", "新视频")],
                ["upload_success", l("Đăng thành công", "Upload successful", "发布成功")],
                ["upload_failure", l("Đăng thất bại", "Upload failed", "发布失败")],
                ["high_ram", l("RAM quá cao", "High RAM usage", "内存占用过高")],
                ["job_confirmation", l("Job cần xác nhận", "Job needs confirmation", "任务需要确认")],
              ] as const
            ).map(([type, label]) => (
              <label key={type}>
                <input
                  type="checkbox"
                  checked={settings.TELEGRAM_NOTIFICATION_TYPES?.[type] !== false}
                  onChange={(event) => updateNotificationPreference(type, event.target.checked)}
                />
                {label}
              </label>
            ))}
          </div>
        </div>
      </section>
      <section className="form-section">
        <h3>{l("Giới hạn song song", "Concurrency limits", "并发限制")}</h3>
        <p className="settings-help">
          {l(
            "Tăng số tiến trình có thể làm quá trình upload nhanh hơn nhưng sử dụng nhiều RAM và CPU hơn.",
            "Increasing the number of processes can make uploads faster, but uses more RAM and CPU.",
            "增加进程数量可能加快上传，但会占用更多内存和 CPU。",
          )}
        </p>
        <div className="form-grid">
          <label>
            <span>{l("Hồ sơ đang tải video", "Profiles downloading videos", "正在下载视频的配置文件")}</span>
            <input
              type="number"
              min="1"
              max="32"
              value={settings.MAX_CONCURRENT_DOWNLOADS ?? 2}
              onChange={(event) => update({ MAX_CONCURRENT_DOWNLOADS: Math.max(1, Number(event.target.value) || 1) })}
            />
          </label>
          <label>
            <span>{l("Tiến trình FFmpeg", "FFmpeg processes", "FFmpeg 进程")}</span>
            <input
              type="number"
              min="1"
              max="32"
              value={settings.MAX_CONCURRENT_FFMPEG ?? 1}
              onChange={(event) => update({ MAX_CONCURRENT_FFMPEG: Math.max(1, Number(event.target.value) || 1) })}
            />
          </label>
          <label>
            <span>{l("Hồ sơ đang đăng video", "Profiles publishing videos", "正在发布视频的配置文件")}</span>
            <input
              type="number"
              min="1"
              max="32"
              value={settings.MAX_CONCURRENT_UPLOADS ?? 2}
              onChange={(event) => update({ MAX_CONCURRENT_UPLOADS: Math.max(1, Number(event.target.value) || 1) })}
            />
          </label>
        </div>
      </section>
      <div className="settings-actions">
        {error && <span className="danger-text">{error}</span>}
        {saved && <span className="success-text">{l("Đã lưu cài đặt", "Settings saved", "设置已保存")}</span>}
        <button className="primary-button" onClick={() => void save()} disabled={saving}>
          <Save size={16} />
          {saving ? l("Đang lưu", "Saving", "正在保存") : l("Lưu thay đổi", "Save changes", "保存更改")}
        </button>
      </div>
    </div>
  );
}

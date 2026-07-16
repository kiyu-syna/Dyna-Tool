import { Save } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../api";
import { EmptyState, SkeletonRows } from "../components/Common";
import type { AppSettings } from "../types";

export default function SettingsPage({ theme, language, onTheme, onLanguage }: {
  theme: "light" | "dark";
  language: "vi" | "en";
  onTheme(value: "light" | "dark"): void;
  onLanguage(value: "vi" | "en"): void;
}) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    void request<{ settings: AppSettings }>("/api/settings")
      .then((result) => setSettings(result.settings))
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)));
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
      const result = await request<{ settings: AppSettings }>("/api/settings", { method: "PUT", body: { settings: next } });
      setSettings(result.settings);
      setSaved(true);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  if (!settings && !error) return <SkeletonRows count={8} />;
  if (!settings) return <EmptyState error message={error} />;
  return <div className="settings-page">
    <section className="form-section"><h3>Giao diện</h3><div className="settings-row"><div><strong>Chủ đề</strong><span>Màu sắc giao diện ứng dụng</span></div><div className="segmented"><button className={theme === "light" ? "selected" : ""} onClick={() => onTheme("light")}>Sáng</button><button className={theme === "dark" ? "selected" : ""} onClick={() => onTheme("dark")}>Tối</button></div></div>
      <div className="settings-row"><div><strong>Ngôn ngữ</strong><span>Ngôn ngữ hiển thị mặc định</span></div><div className="segmented"><button className={language === "vi" ? "selected" : ""} onClick={() => onLanguage("vi")}>Tiếng Việt</button><button className={language === "en" ? "selected" : ""} onClick={() => onLanguage("en")}>English</button></div></div>
    </section>
    <section className="form-section"><h3>Kết nối dịch vụ</h3><div className="form-grid">
      <label><span>GemLogin API URL</span><input value={settings.API_URL || ""} onChange={(event) => update({ API_URL: event.target.value })} /></label>
      <label><span>Payment API URL</span><input value={settings.PAYMENT_API_URL || ""} onChange={(event) => update({ PAYMENT_API_URL: event.target.value })} /></label>
    </div></section>
    <section className="form-section"><h3>Thông báo Telegram</h3><div className="form-grid">
      <label className="full"><span>Bot Token</span><input value={settings.TELEGRAM_BOT_TOKEN || ""} onChange={(event) => update({ TELEGRAM_BOT_TOKEN: event.target.value })} /></label>
      <label><span>Chat ID</span><input value={settings.TELEGRAM_CHAT_ID || ""} onChange={(event) => update({ TELEGRAM_CHAT_ID: event.target.value })} /></label>
      <label className="switch-row"><input type="checkbox" checked={settings.TELEGRAM_QUIET_HOURS_ENABLED === true} onChange={(event) => update({ TELEGRAM_QUIET_HOURS_ENABLED: event.target.checked })} /><span>Bật giờ im lặng</span></label>
      <label><span>Từ giờ</span><input type="time" value={settings.TELEGRAM_QUIET_START || "22:00"} onChange={(event) => update({ TELEGRAM_QUIET_START: event.target.value })} /></label>
      <label><span>Đến giờ</span><input type="time" value={settings.TELEGRAM_QUIET_END || "07:00"} onChange={(event) => update({ TELEGRAM_QUIET_END: event.target.value })} /></label>
    </div></section>
    <section className="form-section"><h3>Giới hạn song song</h3><div className="form-grid">
      <label><span>Profile đang tải video</span><input type="number" min="1" max="32" value={settings.MAX_CONCURRENT_DOWNLOADS ?? 2} onChange={(event) => update({ MAX_CONCURRENT_DOWNLOADS: Math.max(1, Number(event.target.value) || 1) })} /></label>
      <label><span>Tiến trình FFmpeg</span><input type="number" min="1" max="32" value={settings.MAX_CONCURRENT_FFMPEG ?? 1} onChange={(event) => update({ MAX_CONCURRENT_FFMPEG: Math.max(1, Number(event.target.value) || 1) })} /></label>
      <label><span>Profile đang upload</span><input type="number" min="1" max="32" value={settings.MAX_CONCURRENT_UPLOADS ?? 2} onChange={(event) => update({ MAX_CONCURRENT_UPLOADS: Math.max(1, Number(event.target.value) || 1) })} /></label>
    </div></section>
    <div className="settings-actions">{error && <span className="danger-text">{error}</span>}{saved && <span className="success-text">Đã lưu cài đặt</span>}<button className="primary-button" onClick={() => void save()} disabled={saving}><Save size={16} />{saving ? "Đang lưu" : "Lưu thay đổi"}</button></div>
  </div>;
}

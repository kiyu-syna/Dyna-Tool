import {
  BriefcaseBusiness,
  Crown,
  ChevronLeft,
  FileText,
  LayoutDashboard,
  LogOut,
  Settings,
  SlidersHorizontal,
  Workflow,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { request } from "./api";
import appIcon from "./assets/dyna-app-icon.png";
import appMark from "./assets/dyna-mark.png";
import { usePolling } from "./hooks";
import AuthPage from "./pages/AuthPage";
import LogsPage from "./pages/LogsPage";
import OverviewPage from "./pages/OverviewPage";
import PremiumPage from "./pages/PremiumPage";
import ProfilesPage from "./pages/ProfilesPage";
import SettingsPage from "./pages/SettingsPage";
import TrackingPage from "./pages/TrackingPage";
import type { AppSettings, AuthStatus, AuthUser, BusyModeState } from "./types";

type PageKey = "overview" | "tracking" | "profiles" | "logs" | "premium" | "settings";
type SessionState = "checking" | "authenticated" | "anonymous" | "error";

const pageMeta: Record<PageKey, { title: string; subtitle: string; titleEn: string; subtitleEn: string }> = {
  overview: { title: "Tổng quan", subtitle: "Hiệu suất đăng video và tình trạng vận hành", titleEn: "Overview", subtitleEn: "Publishing performance and runtime health" },
  tracking: { title: "Tracking Douyin", subtitle: "Theo dõi Profile và xử lý hàng đợi video", titleEn: "Douyin Tracking", subtitleEn: "Profile monitoring and video queue" },
  profiles: { title: "Cấu hình Profile", subtitle: "Nguồn Douyin, bộ lọc và nền tảng đăng", titleEn: "Profile Settings", subtitleEn: "Douyin sources, filters and publishing platforms" },
  logs: { title: "Nhật ký hoạt động", subtitle: "Dòng sự kiện và lỗi hệ thống theo thời gian thực", titleEn: "Activity Logs", subtitleEn: "Live system events and errors" },
  premium: { title: "Premium", subtitle: "Bản quyền, gói sử dụng và thanh toán", titleEn: "Premium", subtitleEn: "License, subscription plans and payment" },
  settings: { title: "Cài đặt", subtitle: "Giao diện, GemLogin và thông báo Telegram", titleEn: "Settings", subtitleEn: "Appearance, GemLogin and Telegram notifications" },
};

export default function App() {
  const [page, setPage] = useState<PageKey>("overview");
  const [collapsed, setCollapsed] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">(() => (localStorage.getItem("dyna-theme") === "dark" ? "dark" : "light"));
  const [language, setLanguage] = useState<"vi" | "en">(() => (localStorage.getItem("dyna-language") === "en" ? "en" : "vi"));
  const [backendState, setBackendState] = useState("starting");
  const [sessionState, setSessionState] = useState<SessionState>("checking");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sessionError, setSessionError] = useState("");
  const [busySaving, setBusySaving] = useState(false);
  const busyMode = usePolling<{ state: BusyModeState }>("/api/busy-mode", 2000);
  const isBusy = busyMode.data?.state.busy === true;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("dyna-theme", theme);
  }, [theme]);
  useEffect(() => localStorage.setItem("dyna-language", language), [language]);
  useEffect(() => {
    void window.dyna?.backendInfo().then((info) => setBackendState(String(info.state || "starting")));
    return window.dyna?.onBackendStatus((status) => setBackendState(String(status.state || "failed")));
  }, []);
  useEffect(() => {
    void request<{ settings: AppSettings }>("/api/settings").then(({ settings }) => {
      if (settings.UI_THEME === "light" || settings.UI_THEME === "dark") setTheme(settings.UI_THEME);
      if (settings.UI_LANGUAGE === "vi" || settings.UI_LANGUAGE === "en") setLanguage(settings.UI_LANGUAGE);
    }).catch(() => undefined);
  }, []);
  useEffect(() => {
    void checkSession();
  }, []);

  async function checkSession() {
    setSessionState("checking");
    setSessionError("");
    try {
      const result = await request<AuthStatus>("/api/auth/status");
      setUser(result.user);
      setSessionState(result.authenticated && result.user ? "authenticated" : "anonymous");
    } catch (caught) {
      setSessionError(caught instanceof Error ? caught.message : String(caught));
      setSessionState("error");
    }
  }

  function authenticated(nextUser: AuthUser) {
    setUser(nextUser);
    setSessionState("authenticated");
    setPage("overview");
  }

  async function logout() {
    try {
      await request<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
    } finally {
      setUser(null);
      setPage("overview");
      setSessionState("anonymous");
    }
  }

  async function toggleBusyMode() {
    if (busySaving) return;
    setBusySaving(true);
    try {
      const result = await request<{ state: BusyModeState }>("/api/busy-mode", {
        method: "PUT",
        body: { busy: !isBusy },
      });
      busyMode.setData(result);
    } catch {
      await busyMode.refresh();
    } finally {
      setBusySaving(false);
    }
  }

  const navigation = useMemo(() => [
    { key: "overview" as const, label: language === "en" ? "Overview" : "Tổng quan", icon: LayoutDashboard },
    { key: "tracking" as const, label: language === "en" ? "Douyin Tracking" : "Tracking Douyin", icon: Workflow },
    { key: "profiles" as const, label: language === "en" ? "Profile Settings" : "Cấu hình Profile", icon: SlidersHorizontal },
    { key: "logs" as const, label: language === "en" ? "Activity Logs" : "Nhật ký", icon: FileText },
    { key: "premium" as const, label: "Premium", icon: Crown },
    { key: "settings" as const, label: language === "en" ? "Settings" : "Cài đặt", icon: Settings },
  ], [language]);
  const meta = pageMeta[page];

  if (sessionState === "checking") return <main className="startup-shell"><div className="startup-mark"><img src={appIcon} alt="" /></div><strong>Dyna</strong><span>{language === "en" ? "Checking your session..." : "Đang kiểm tra phiên đăng nhập..."}</span></main>;
  if (sessionState === "error") return <main className="startup-shell error"><div className="startup-mark"><img src={appIcon} alt="" /></div><strong>{language === "en" ? "Cannot start Dyna" : "Không thể khởi động Dyna"}</strong><span>{sessionError}</span><button className="primary-button" onClick={() => void checkSession()}>{language === "en" ? "Try again" : "Thử lại"}</button></main>;
  if (sessionState === "anonymous") return <AuthPage language={language} onAuthenticated={authenticated} />;

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><img src={appMark} alt="" /></span><div><strong>Dyna</strong><small>Automation Console</small></div></div>
        <nav>{navigation.map(({ key, label, icon: Icon }) => (
          <button key={key} className={page === key ? "active" : ""} onClick={() => setPage(key)} title={collapsed ? label : undefined}>
            <Icon size={18} /><span>{label}</span>
          </button>
        ))}</nav>
        <div className="sidebar-footer">
          <button
            className={`busy-toggle ${isBusy ? "active" : ""}`}
            onClick={() => void toggleBusyMode()}
            disabled={busySaving}
            aria-pressed={isBusy}
            title={isBusy ? (language === "en" ? "Busy: use default captions" : "Đang bận: dùng mô tả mặc định") : (language === "en" ? "Free: ask for Telegram captions" : "Đang rảnh: hỏi caption qua Telegram")}
          >
            <BriefcaseBusiness size={17} />
            <span className="busy-copy"><strong>{language === "en" ? "Busy" : "Bận"}</strong><small>{isBusy ? (language === "en" ? "Default captions" : "Dùng mô tả mặc định") : (language === "en" ? "Telegram captions" : "Chờ caption Telegram")}</small></span>
            <span className="busy-switch" aria-hidden="true"><i /></span>
          </button>
          <div className="account-compact" title={user?.username || ""}><span>{(user?.display_name || user?.username || "U").slice(0, 1).toUpperCase()}</span><div><strong>{user?.display_name || user?.username}</strong><small>{language === "en" ? "Signed in" : "Đã đăng nhập"}</small></div><button className="icon-button" onClick={() => void logout()} title={language === "en" ? "Sign out" : "Đăng xuất"}><LogOut size={15} /></button></div>
          <button className="collapse-button" onClick={() => setCollapsed((value) => !value)} title={collapsed ? "Mở rộng" : "Thu gọn"}><ChevronLeft size={17} /><span>{language === "en" ? "Collapse" : "Thu gọn"}</span></button>
          <div className="version"><i />v0.1 desktop</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div><h1>{language === "en" ? meta.titleEn : meta.title}</h1><p>{language === "en" ? meta.subtitleEn : meta.subtitle}</p></div>
          <div className={`backend-state ${backendState}`}><i />{backendState === "ready" ? (language === "en" ? "System ready" : "Hệ thống sẵn sàng") : (language === "en" ? "Connecting" : "Đang kết nối")}</div>
        </header>
        <div className="content-scroll">
          {page === "overview" && <OverviewPage />}
          {page === "tracking" && <TrackingPage />}
          {page === "profiles" && <ProfilesPage />}
          {page === "logs" && <LogsPage />}
          {page === "premium" && <PremiumPage language={language} />}
          {page === "settings" && <SettingsPage theme={theme} language={language} onTheme={setTheme} onLanguage={setLanguage} />}
        </div>
      </main>
    </div>
  );
}

import { lazy, Suspense, useEffect, useState } from "react";
import BrowserPage from "../features/browser/BrowserPage";
import { TerminalLogs } from "../features/log-viewer/TerminalLogsWindow";
import OverviewPage from "../features/overview/OverviewPage";
import PremiumPage from "../features/premium/PremiumPage";
import ProfilesPage from "../features/profiles/ProfilesPage";
import PublishCenterPage from "../features/publishing/PublishCenterPage";
import SettingsPage from "../features/settings/SettingsPage";
import TrackingPage from "../features/tracking/TrackingPage";
import type { Language } from "../shared/i18n";
import Sidebar from "./Sidebar";
import type { PageKey, Theme } from "./types";
import WeChatDialog from "./WeChatDialog";
import WindowTitleBar from "./WindowTitleBar";

const AssistantWidget = lazy(() => import("../features/assistant/AssistantWidget"));

interface AppShellProps {
  theme: Theme;
  language: Language;
  onTheme(theme: Theme): void;
  onLanguage(language: Language): void;
}

export default function AppShell({
  theme,
  language,
  onTheme,
  onLanguage,
}: AppShellProps) {
  const [page, setPage] = useState<PageKey>("overview");
  const [visitedPages, setVisitedPages] = useState<Set<PageKey>>(() => new Set(["overview"]));
  const [collapsed, setCollapsed] = useState(false);
  const [wechatQrOpen, setWechatQrOpen] = useState(false);

  function navigate(nextPage: PageKey) {
    setVisitedPages((current) => {
      if (current.has(nextPage)) return current;
      const next = new Set(current);
      next.add(nextPage);
      return next;
    });
    setPage(nextPage);
  }

  useEffect(() => {
    return window.dyna?.onNavigate?.((nextPage) => {
      if (
        nextPage === "overview" ||
        nextPage === "tracking" ||
        nextPage === "publish" ||
        nextPage === "profiles" ||
        nextPage === "browser" ||
        nextPage === "logs" ||
        nextPage === "premium" ||
        nextPage === "settings"
      ) {
        navigate(nextPage);
      }
    });
  }, []);

  return (
    <>
      <WindowTitleBar />
      <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
        <Sidebar
          page={page}
          collapsed={collapsed}
          onPage={navigate}
          onCollapse={() => setCollapsed((value) => !value)}
          onOpenWeChat={() => setWechatQrOpen(true)}
        />
        <main className="main-area">
          <div className="content-scroll">
            {visitedPages.has("overview") && (
              <div hidden={page !== "overview"}><OverviewPage onOpenTracking={() => navigate("tracking")} /></div>
            )}
            {visitedPages.has("tracking") && <div hidden={page !== "tracking"}><TrackingPage /></div>}
            {visitedPages.has("publish") && <div hidden={page !== "publish"}><PublishCenterPage /></div>}
            {visitedPages.has("profiles") && <div hidden={page !== "profiles"}><ProfilesPage /></div>}
            {visitedPages.has("browser") && <div hidden={page !== "browser"}><BrowserPage /></div>}
            {visitedPages.has("logs") && <div hidden={page !== "logs"}><TerminalLogs embedded /></div>}
            {visitedPages.has("premium") && <div hidden={page !== "premium"}><PremiumPage /></div>}
            {visitedPages.has("settings") && (
              <div hidden={page !== "settings"}>
                <SettingsPage theme={theme} language={language} onTheme={onTheme} onLanguage={onLanguage} />
              </div>
            )}
          </div>
        </main>
      </div>
      <Suspense fallback={null}>
        <AssistantWidget />
      </Suspense>
      {wechatQrOpen && <WeChatDialog onClose={() => setWechatQrOpen(false)} />}
    </>
  );
}

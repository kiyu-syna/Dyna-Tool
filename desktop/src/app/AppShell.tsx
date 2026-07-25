import { lazy, Suspense, useState } from "react";
import OverviewPage from "../features/overview/OverviewPage";
import type { Language } from "../shared/i18n";
import type { AuthUser } from "../shared/types";
import Sidebar from "./Sidebar";
import type { PageKey, Theme } from "./types";
import WeChatDialog from "./WeChatDialog";
import WindowTitleBar from "./WindowTitleBar";

const AssistantWidget = lazy(() => import("../features/assistant/AssistantWidget"));
const BrowserPage = lazy(() => import("../features/browser/BrowserPage"));
const PremiumPage = lazy(() => import("../features/premium/PremiumPage"));
const ProfilesPage = lazy(() => import("../features/profiles/ProfilesPage"));
const PublishCenterPage = lazy(() => import("../features/publishing/PublishCenterPage"));
const SettingsPage = lazy(() => import("../features/settings/SettingsPage"));
const TrackingPage = lazy(() => import("../features/tracking/TrackingPage"));

function PageFallback() {
  return (
    <div className="page-load-fallback" aria-label="Đang tải trang">
      <span />
    </div>
  );
}

interface AppShellProps {
  user: AuthUser;
  licenseLabel: string;
  theme: Theme;
  language: Language;
  onTheme(theme: Theme): void;
  onLanguage(language: Language): void;
  onLogout(): void;
}

export default function AppShell({
  user,
  licenseLabel,
  theme,
  language,
  onTheme,
  onLanguage,
  onLogout,
}: AppShellProps) {
  const [page, setPage] = useState<PageKey>("overview");
  const [collapsed, setCollapsed] = useState(false);
  const [wechatQrOpen, setWechatQrOpen] = useState(false);

  return (
    <>
      <WindowTitleBar />
      <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
        <Sidebar
          page={page}
          collapsed={collapsed}
          user={user}
          licenseLabel={licenseLabel}
          onPage={setPage}
          onCollapse={() => setCollapsed((value) => !value)}
          onLogout={onLogout}
          onOpenWeChat={() => setWechatQrOpen(true)}
        />
        <main className="main-area">
          <div className="content-scroll">
            <Suspense fallback={<PageFallback />}>
              {page === "overview" && <OverviewPage onOpenTracking={() => setPage("tracking")} />}
              {page === "tracking" && <TrackingPage />}
              {page === "publish" && <PublishCenterPage />}
              {page === "profiles" && <ProfilesPage />}
              {page === "browser" && <BrowserPage />}
              {page === "premium" && <PremiumPage />}
              {page === "settings" && (
                <SettingsPage theme={theme} language={language} onTheme={onTheme} onLanguage={onLanguage} />
              )}
            </Suspense>
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

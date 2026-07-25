import { useEffect, useState } from "react";
import { request } from "../shared/api/client";
import appIcon from "../assets/dyna-app-icon.png";
import { usePolling } from "../shared/hooks/usePolling";
import { languageTag, useI18n, type Language } from "../shared/i18n";
import AuthPage from "../features/auth/AuthPage";
import type { AppSettings, AuthStatus, AuthUser, LicenseStatus } from "../shared/types";
import AppShell from "./AppShell";
import type { SessionState, Theme } from "./types";

export default function DynaApp({ language, onLanguage }: { language: Language; onLanguage(value: Language): void }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const savedTheme = localStorage.getItem("dyna-theme");
    return savedTheme === "light" || savedTheme === "dark" || savedTheme === "system" ? savedTheme : "system";
  });
  const [sessionState, setSessionState] = useState<SessionState>("checking");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sessionError, setSessionError] = useState("");
  const license = usePolling<LicenseStatus>("/api/license?refresh=false", 60_000);
  const { l, t } = useI18n();

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      document.documentElement.dataset.theme = theme === "system" ? (media.matches ? "dark" : "light") : theme;
    };
    applyTheme();
    media.addEventListener("change", applyTheme);
    localStorage.setItem("dyna-theme", theme);
    return () => media.removeEventListener("change", applyTheme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("dyna-language", language);
    document.documentElement.lang = languageTag(language);
  }, [language]);

  useEffect(() => {
    void request<{ settings: AppSettings }>("/api/settings")
      .then(({ settings }) => {
        if (settings.UI_THEME === "system" || settings.UI_THEME === "light" || settings.UI_THEME === "dark")
          setTheme(settings.UI_THEME);
        if (
          settings.UI_LANGUAGE === "vi" ||
          settings.UI_LANGUAGE === "en" ||
          settings.UI_LANGUAGE === "zh" ||
          settings.UI_LANGUAGE === "zh-TW"
        )
          onLanguage(settings.UI_LANGUAGE);
      })
      .catch(() => undefined);
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
  }

  async function logout() {
    try {
      await request<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
    } finally {
      setUser(null);
      setSessionState("anonymous");
    }
  }

  const licenseLabel = license.data?.is_active
    ? license.data.info.plan_name || "Premium"
    : l("Chưa có gói", "No plan", "未订阅");

  if (sessionState === "checking") {
    return (
      <main className="startup-shell">
        <div className="startup-mark">
          <img src={appIcon} alt="" />
        </div>
        <strong>Dyna</strong>
        <span>{t("app.session.checking")}</span>
      </main>
    );
  }
  if (sessionState === "error") {
    return (
      <main className="startup-shell error">
        <div className="startup-mark">
          <img src={appIcon} alt="" />
        </div>
        <strong>{t("app.startup.failed")}</strong>
        <span>{sessionError}</span>
        <button className="primary-button" onClick={() => void checkSession()}>
          {t("common.retry")}
        </button>
      </main>
    );
  }
  if (sessionState === "anonymous" || !user) return <AuthPage onAuthenticated={authenticated} />;

  return (
    <AppShell
      user={user}
      licenseLabel={licenseLabel}
      theme={theme}
      language={language}
      onTheme={setTheme}
      onLanguage={onLanguage}
      onLogout={() => void logout()}
    />
  );
}

import { ArrowRight, KeyRound, UserPlus } from "lucide-react";
import { FormEvent, useState } from "react";
import { request } from "../../shared/api/client";
import appLogo from "../../assets/dyna-logo.png";
import { useI18n } from "../../shared/i18n";
import type { AuthUser } from "../../shared/types";
import "./auth.css";

type AuthMode = "login" | "register";

export default function AuthPage({ onAuthenticated }: { onAuthenticated(user: AuthUser): void }) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const { t } = useI18n();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const endpoint = mode === "login" ? "/api/auth/login" : "/api/auth/register";
      const body = mode === "login" ? { username, password } : { phone, username, password };
      const result = await request<{ ok: boolean; user: AuthUser }>(endpoint, { method: "POST", body });
      onAuthenticated(result.user);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSubmitting(false);
    }
  }

  function changeMode(next: AuthMode) {
    setMode(next);
    setError("");
  }

  return (
    <main className="auth-shell">
      <section className="auth-panel" aria-label={t("auth.region")}>
        <header className="auth-brand">
          <img src={appLogo} alt="Dyna" />
          <small>{t("auth.console")}</small>
        </header>
        <div className="auth-heading">
          <h1>{mode === "login" ? t("auth.sign_in") : t("auth.register.title")}</h1>
          <p>{mode === "login" ? t("auth.sign_in.description") : t("auth.register.description")}</p>
        </div>
        <div className="segmented auth-segmented">
          <button type="button" className={mode === "login" ? "selected" : ""} onClick={() => changeMode("login")}>
            <KeyRound size={15} />
            {t("auth.sign_in")}
          </button>
          <button
            type="button"
            className={mode === "register" ? "selected" : ""}
            onClick={() => changeMode("register")}
          >
            <UserPlus size={15} />
            {t("auth.register")}
          </button>
        </div>
        <form className="auth-form" onSubmit={submit}>
          {mode === "register" && (
            <label>
              <span>{t("auth.phone")}</span>
              <input
                type="tel"
                autoComplete="tel"
                required
                minLength={9}
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
              />
            </label>
          )}
          <label>
            <span>{t("auth.username")}</span>
            <input
              autoComplete="username"
              required
              minLength={3}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            <span>{t("auth.password")}</span>
            <input
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
              minLength={6}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error && (
            <div className="auth-error" role="alert">
              {error}
            </div>
          )}
          <button className="primary-button auth-submit" type="submit" disabled={submitting}>
            {submitting ? t("common.processing") : mode === "login" ? t("auth.sign_in") : t("auth.register.title")}
            <ArrowRight size={16} />
          </button>
        </form>
      </section>
    </main>
  );
}

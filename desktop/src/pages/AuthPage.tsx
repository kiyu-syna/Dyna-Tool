import { ArrowRight, KeyRound, UserPlus } from "lucide-react";
import { FormEvent, useState } from "react";
import { request } from "../api";
import appLogo from "../assets/dyna-logo.png";
import type { AuthUser } from "../types";

type AuthMode = "login" | "register";

export default function AuthPage({
  language,
  onAuthenticated,
}: {
  language: "vi" | "en";
  onAuthenticated(user: AuthUser): void;
}) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const vi = language === "vi";

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

  return <main className="auth-shell">
    <section className="auth-panel" aria-label={vi ? "Đăng nhập Dyna" : "Sign in to Dyna"}>
      <header className="auth-brand">
        <img src={appLogo} alt="Dyna" />
        <small>Automation Console</small>
      </header>
      <div className="auth-heading">
        <h1>{mode === "login" ? (vi ? "Đăng nhập" : "Sign in") : (vi ? "Tạo tài khoản" : "Create account")}</h1>
        <p>{mode === "login" ? (vi ? "Tiếp tục phiên quản lý tự động hóa của bạn." : "Continue to your automation workspace.") : (vi ? "Đăng ký tài khoản để sử dụng Dyna." : "Register an account to use Dyna.")}</p>
      </div>
      <div className="segmented auth-segmented">
        <button type="button" className={mode === "login" ? "selected" : ""} onClick={() => changeMode("login")}><KeyRound size={15} />{vi ? "Đăng nhập" : "Sign in"}</button>
        <button type="button" className={mode === "register" ? "selected" : ""} onClick={() => changeMode("register")}><UserPlus size={15} />{vi ? "Đăng ký" : "Register"}</button>
      </div>
      <form className="auth-form" onSubmit={submit}>
        {mode === "register" && <label><span>{vi ? "Số điện thoại" : "Phone number"}</span><input type="tel" autoComplete="tel" required minLength={9} value={phone} onChange={(event) => setPhone(event.target.value)} /></label>}
        <label><span>{vi ? "Tên đăng nhập" : "Username"}</span><input autoComplete="username" required minLength={3} value={username} onChange={(event) => setUsername(event.target.value)} /></label>
        <label><span>{vi ? "Mật khẩu" : "Password"}</span><input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={6} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        {error && <div className="auth-error" role="alert">{error}</div>}
        <button className="primary-button auth-submit" type="submit" disabled={submitting}>
          {submitting ? (vi ? "Đang xử lý..." : "Please wait...") : (mode === "login" ? (vi ? "Đăng nhập" : "Sign in") : (vi ? "Tạo tài khoản" : "Create account"))}<ArrowRight size={16} />
        </button>
      </form>
    </section>
  </main>;
}

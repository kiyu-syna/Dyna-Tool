import type { KeyboardEvent } from "react";
import { useI18n } from "../../../shared/i18n";
import type { ProfileConfig } from "../../../shared/types";
import type { ProxyEndpoint } from "../types";

interface ProxySettingsProps {
  profile: ProfileConfig;
  endpoint: ProxyEndpoint;
  enabled: boolean;
  quickValue: string;
  onQuickValue(value: string): void;
  onApplyQuick(): void;
  onSelectType(value: string): void;
  onUpdate(field: string, value: unknown): void;
  onUpdateEndpoint(changes: Partial<ProxyEndpoint>): void;
}

export default function ProxySettings({
  profile,
  endpoint,
  enabled,
  quickValue,
  onQuickValue,
  onApplyQuick,
  onSelectType,
  onUpdate,
  onUpdateEndpoint,
}: ProxySettingsProps) {
  const { l } = useI18n();
  const proxy = profile.browser?.proxy;

  function applyOnEnter(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    onApplyQuick();
  }

  return (
    <div className="browser-proxy-settings">
      <div className="proxy-form">
        <label className="proxy-field proxy-full">
          <span>{l("Loại Proxy", "Proxy type", "代理类型")}</span>
          <select value={enabled ? endpoint.scheme : "disabled"} onChange={(event) => onSelectType(event.target.value)}>
            <option value="disabled">{l("Không sử dụng", "Disabled", "不使用")}</option>
            <option value="http">HTTP</option>
            <option value="https">HTTPS</option>
            <option value="socks5">SOCKS5</option>
          </select>
        </label>
        {enabled && (
          <>
            <label className="proxy-field proxy-full">
              <span>Proxy</span>
              <input
                className="mono"
                value={quickValue}
                onChange={(event) => onQuickValue(event.target.value)}
                onBlur={onApplyQuick}
                onKeyDown={applyOnEnter}
                placeholder="host:port:username:password"
              />
            </label>
            <label className="proxy-field proxy-host">
              <span>{l("Máy chủ", "Host", "主机")}</span>
              <input
                className="mono"
                value={endpoint.host}
                onChange={(event) => onUpdateEndpoint({ host: event.target.value })}
                placeholder="Host"
              />
            </label>
            <label className="proxy-field proxy-port">
              <span>{l("Cổng", "Port", "端口")}</span>
              <input
                className="mono"
                inputMode="numeric"
                value={endpoint.port}
                onChange={(event) => onUpdateEndpoint({ port: event.target.value.replace(/\D/g, "") })}
                placeholder="Port"
              />
            </label>
            <label className="proxy-field proxy-full">
              <span>{l("Tên đăng nhập", "Username", "用户名")}</span>
              <input
                value={proxy?.username || ""}
                onChange={(event) => onUpdate("username", event.target.value)}
                placeholder="Username"
                autoComplete="off"
              />
            </label>
            <label className="proxy-field proxy-full">
              <span>{l("Mật khẩu", "Password", "密码")}</span>
              <input
                type="password"
                value={proxy?.password || ""}
                onChange={(event) => onUpdate("password", event.target.value)}
                placeholder={
                  proxy?.password_set
                    ? l("Đã lưu · nhập để thay đổi", "Saved · enter to replace", "已保存 · 输入以替换")
                    : "Password"
                }
                autoComplete="new-password"
              />
              {proxy?.password_set && !proxy.password && (
                <button type="button" className="proxy-password-clear" onClick={() => onUpdate("password_set", false)}>
                  {l("Xóa mật khẩu đã lưu", "Clear saved password", "清除已保存的密码")}
                </button>
              )}
            </label>
            <label className="proxy-field proxy-full">
              <span>{l("Bỏ qua proxy (không bắt buộc)", "Proxy bypass (optional)", "绕过代理（可选）")}</span>
              <input
                className="mono"
                value={proxy?.bypass || ""}
                onChange={(event) => onUpdate("bypass", event.target.value)}
                placeholder="localhost, 127.0.0.1, *.internal"
              />
            </label>
          </>
        )}
      </div>
      {enabled && (
        <small className="proxy-safety-copy">
          {l(
            "Proxy được áp dụng sau khi mở lại trình duyệt. Nếu proxy mất kết nối, Dyna không tự chuyển sang IP thật.",
            "The proxy applies after reopening the browser. If unavailable, Dyna will not fall back to the direct IP.",
            "重新打开浏览器后代理生效。代理不可用时，Dyna 不会回退到真实 IP。",
          )}
        </small>
      )}
    </div>
  );
}

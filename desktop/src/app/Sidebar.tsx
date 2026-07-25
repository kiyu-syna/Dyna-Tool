import { ChevronLeft, Crown, LayoutDashboard, LogOut, Send, Settings } from "lucide-react";
import { useMemo } from "react";
import appMark from "../assets/dyna-mark.png";
import facebookIcon from "../assets/facebook-icon.png";
import wechatIcon from "../assets/wechat-icon.png";
import { useI18n } from "../shared/i18n";
import type { AuthUser } from "../shared/types";
import { createNavigationGroups } from "./navigation";
import type { PageKey } from "./types";

interface SidebarProps {
  page: PageKey;
  collapsed: boolean;
  user: AuthUser;
  licenseLabel: string;
  onPage(page: PageKey): void;
  onCollapse(): void;
  onLogout(): void;
  onOpenWeChat(): void;
}

export default function Sidebar({
  page,
  collapsed,
  user,
  licenseLabel,
  onPage,
  onCollapse,
  onLogout,
  onOpenWeChat,
}: SidebarProps) {
  const { l } = useI18n();
  const navigationGroups = useMemo(() => createNavigationGroups(l), [l]);

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">
          <img src={appMark} alt="" />
        </span>
        <div>
          <strong>Dyna</strong>
        </div>
      </div>
      <nav>
        <section className="nav-primary">
          <button
            className={page === "overview" ? "active" : ""}
            onClick={() => onPage("overview")}
            title={collapsed ? l("Tổng quan", "Overview", "总览") : undefined}
          >
            <LayoutDashboard size={18} />
            <span className="nav-item-copy">
              <strong>{l("Tổng quan", "Overview", "总览")}</strong>
            </span>
          </button>
        </section>
        {navigationGroups.map((group) => (
          <section className="nav-group" key={group.label}>
            <h2>{group.label}</h2>
            {group.items.map(({ key, label, icon: Icon }) => {
              const opensLogWindow = key === "logs";
              return (
                <button
                  key={key}
                  className={!opensLogWindow && page === key ? "active" : ""}
                  onClick={() => {
                    if (opensLogWindow) void window.dyna?.openLogWindow();
                    else onPage(key);
                  }}
                  title={collapsed ? label : undefined}
                >
                  <Icon size={18} />
                  <span className="nav-item-copy">
                    <strong>{label}</strong>
                  </span>
                </button>
              );
            })}
          </section>
        ))}
      </nav>
      <div className="sidebar-footer">
        <button
          className={`sidebar-footer-nav ${page === "settings" ? "active" : ""}`}
          onClick={() => onPage("settings")}
          title={collapsed ? l("Cài đặt", "Settings", "设置") : undefined}
        >
          <Settings size={18} />
          <span>{l("Cài đặt", "Settings", "设置")}</span>
        </button>
        <div className="account-compact" title={user.username || ""}>
          <span>{(user.display_name || user.username || "U").slice(0, 1).toUpperCase()}</span>
          <div className="account-copy">
            <strong>{user.display_name || user.username}</strong>
            <small>
              {l("Đã đăng nhập", "Signed in", "已登录")}
              <b aria-hidden="true"> · </b>
              <em>{licenseLabel}</em>
            </small>
          </div>
          <button
            className="account-upgrade"
            onClick={() => onPage("premium")}
            title={l("Xem gói và nâng cấp", "View plans and upgrade", "查看套餐并升级")}
          >
            <Crown size={13} />
            <span>{l("Nâng cấp", "Upgrade", "升级")}</span>
          </button>
          <button className="icon-button" onClick={onLogout} title={l("Đăng xuất", "Sign out", "退出登录")}>
            <LogOut size={15} />
          </button>
        </div>
        <div className="sidebar-support">
          <strong>{l("Hỗ trợ & Góp ý", "Support & Feedback", "支持与反馈")}</strong>
          <div className="sidebar-support-actions">
            <button
              className="support-link telegram"
              onClick={() => void window.dyna?.openExternal("https://t.me/+lSBngWZtELg5NDM9")}
              title="Telegram"
              aria-label="Telegram"
            >
              <Send size={18} />
            </button>
            <button
              className="support-link facebook"
              onClick={() => void window.dyna?.openExternal("https://www.facebook.com/profile.php?id=61592573631104")}
              title="Facebook"
              aria-label="Facebook"
            >
              <img src={facebookIcon} alt="" />
            </button>
            <button className="support-link wechat" onClick={onOpenWeChat} title="WeChat" aria-label="WeChat">
              <img src={wechatIcon} alt="" />
            </button>
          </div>
        </div>
        <button
          className="collapse-button"
          onClick={onCollapse}
          title={collapsed ? l("Mở rộng", "Expand", "展开") : l("Thu gọn", "Collapse", "收起")}
        >
          <ChevronLeft size={17} />
          <span>{l("Thu gọn", "Collapse", "收起")}</span>
        </button>
        <div className="version">
          <i />
          Desktop v0.1.0
        </div>
      </div>
    </aside>
  );
}

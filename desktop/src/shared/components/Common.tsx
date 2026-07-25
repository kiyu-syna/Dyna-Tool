import type { ReactNode } from "react";
import { AlertTriangle, Inbox, X } from "lucide-react";
import { useEffect } from "react";
import { useI18n } from "../i18n";
import type { PlatformKey } from "../types";

export function Section({
  title,
  action,
  children,
  className = "",
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`section ${className}`}>
      <header className="section-header">
        <h2>{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}

export function StatusPill({
  text,
  tone = "neutral",
}: {
  text: string;
  tone?: "success" | "danger" | "warning" | "info" | "neutral";
}) {
  return (
    <span className={`status-pill ${tone}`}>
      <i />
      {text}
    </span>
  );
}

export function PlatformMarks({
  platforms,
  failedPlatforms = [],
}: {
  platforms: Partial<Record<PlatformKey, boolean>> | PlatformKey[];
  failedPlatforms?: PlatformKey[];
}) {
  const { l } = useI18n();
  const enabled = Array.isArray(platforms)
    ? new Set(platforms)
    : new Set(
        Object.entries(platforms)
          .filter(([, value]) => value)
          .map(([key]) => key),
      );
  const failed = new Set(failedPlatforms);
  const mark = (platform: PlatformKey, shortLabel: string, title: string) => (
    <span
      className={`${enabled.has(platform) ? `active ${platform}` : ""}${failed.has(platform) ? " failed" : ""}`}
      title={
        failed.has(platform)
          ? l(`${title} · Đăng thất bại`, `${title} · Publishing failed`, `${title} · 发布失败`)
          : title
      }
    >
      {shortLabel}
    </span>
  );
  return (
    <div className="platform-marks" aria-label={l("Nền tảng đăng", "Publishing platforms", "发布平台")}>
      {mark("tiktok", "TK", "TikTok")}
      {mark("youtube", "YT", "YouTube Shorts")}
      {mark("facebook", "FB", "Facebook Reels")}
    </div>
  );
}

export function EmptyState({ message, error = false }: { message: string; error?: boolean }) {
  const Icon = error ? AlertTriangle : Inbox;
  return (
    <div className={`empty-state ${error ? "error" : ""}`}>
      <Icon size={19} />
      {message}
    </div>
  );
}

export function SkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <div className="skeleton-list">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} />
      ))}
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  className = "",
}: {
  title: string;
  onClose(): void;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  const { l } = useI18n();
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className={`modal-dialog ${className}`} role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} title={l("Đóng", "Close", "关闭")}>
            <X size={17} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  );
}

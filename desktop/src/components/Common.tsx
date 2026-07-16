import type { ReactNode } from "react";
import { AlertTriangle, Inbox, X } from "lucide-react";
import { useEffect } from "react";
import type { PlatformKey } from "../types";

export function Section({ title, action, children, className = "" }: {
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

export function StatusPill({ text, tone = "neutral" }: {
  text: string;
  tone?: "success" | "danger" | "warning" | "info" | "neutral";
}) {
  return <span className={`status-pill ${tone}`}><i />{text}</span>;
}

export function PlatformMarks({ platforms }: {
  platforms: Partial<Record<PlatformKey, boolean>> | PlatformKey[];
}) {
  const enabled = Array.isArray(platforms)
    ? new Set(platforms)
    : new Set(Object.entries(platforms).filter(([, value]) => value).map(([key]) => key));
  return (
    <div className="platform-marks" aria-label="Nền tảng đăng">
      <span className={enabled.has("tiktok") ? "active tiktok" : ""} title="TikTok">TK</span>
      <span className={enabled.has("youtube") ? "active youtube" : ""} title="YouTube Shorts">YT</span>
      <span className={enabled.has("facebook") ? "active facebook" : ""} title="Facebook Reels">FB</span>
    </div>
  );
}

export function EmptyState({ message, error = false }: { message: string; error?: boolean }) {
  const Icon = error ? AlertTriangle : Inbox;
  return <div className={`empty-state ${error ? "error" : ""}`}><Icon size={19} />{message}</div>;
}

export function SkeletonRows({ count = 4 }: { count?: number }) {
  return <div className="skeleton-list">{Array.from({ length: count }, (_, index) => <div key={index} />)}</div>;
}

export function Modal({ title, onClose, children, footer, className = "" }: {
  title: string;
  onClose(): void;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <div className={`modal-dialog ${className}`} role="dialog" aria-modal="true" aria-label={title}>
      <header><h2>{title}</h2><button className="icon-button" onClick={onClose} title="Đóng"><X size={17} /></button></header>
      <div className="modal-body">{children}</div>
      {footer && <footer>{footer}</footer>}
    </div>
  </div>;
}

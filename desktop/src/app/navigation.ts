import { FileText, MonitorCog, SlidersHorizontal, Video, Workflow, type LucideIcon } from "lucide-react";
import type { PageKey } from "./types";

type Localize = (vi: string, en: string, zh: string) => string;

export interface NavigationItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
}

export interface NavigationGroup {
  label: string;
  items: NavigationItem[];
}

export function createNavigationGroups(l: Localize): NavigationGroup[] {
  return [
    {
      label: l("VẬN HÀNH", "OPERATIONS", "运行"),
      items: [
        { key: "tracking", label: l("Theo dõi hồ sơ", "Profile Tracking", "配置文件跟踪"), icon: Workflow },
        { key: "publish", label: l("Đăng video trên máy", "Publish local videos", "发布本机视频"), icon: Video },
      ],
    },
    {
      label: l("THIẾT LẬP", "SETTINGS", "设置"),
      items: [
        { key: "profiles", label: l("Hồ sơ", "Profiles", "配置文件"), icon: SlidersHorizontal },
        { key: "browser", label: l("Trình duyệt", "Browser", "浏览器"), icon: MonitorCog },
      ],
    },
    {
      label: "LOGS",
      items: [{ key: "logs", label: l("Nhật ký", "Logs", "日志"), icon: FileText }],
    },
  ];
}

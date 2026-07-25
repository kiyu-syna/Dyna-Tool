import type { ProfileConfig, PublisherReadyCheck } from "../../../shared/types";
import type { BrowserProvider } from "../types";

type Localize = (vi: string, en: string, zh: string) => string;

export function loginStatusLabel(check: PublisherReadyCheck | undefined, l: Localize) {
  if (!check || check.status === "unknown") return l("Chưa kiểm tra", "Not checked", "未检查");
  return (
    (
      {
        checking: l("Đang kiểm tra", "Checking", "正在检查"),
        ready: l("Đã đăng nhập", "Signed in", "已登录"),
        login_required: l("Cần đăng nhập", "Sign-in required", "需要登录"),
        attention: l("Cần kiểm tra", "Needs attention", "需要检查"),
        error: l("Không kiểm tra được", "Could not check", "无法检查"),
      } as Record<string, string>
    )[check.status] || check.status
  );
}

export function loginStatusTone(check: PublisherReadyCheck | undefined) {
  if (check?.status === "ready") return "success" as const;
  if (check?.status === "checking") return "info" as const;
  if (check?.status === "login_required" || check?.status === "attention") return "warning" as const;
  if (check?.status === "error") return "danger" as const;
  return "neutral" as const;
}

export function providerOf(_profile: ProfileConfig): BrowserProvider {
  return "local_chromium";
}

export function hasCompleteLocalBrowser(profile: ProfileConfig): boolean {
  return Boolean(
    String(profile.browser?.user_data_dir || "").trim() && String(profile.browser?.executable_path || "").trim(),
  );
}

export function mergeBrowserSettings(latest: ProfileConfig, draft: ProfileConfig): ProfileConfig {
  return {
    ...latest,
    browser: { ...(latest.browser || {}), ...(draft.browser || {}) },
    douyin: {
      ...(latest.douyin || {}),
      gemlogin_profile_id: draft.douyin?.gemlogin_profile_id || latest.douyin?.gemlogin_profile_id || latest.id,
    },
    tiktok: {
      ...(latest.tiktok || {}),
      gemlogin_profile_id: draft.tiktok?.gemlogin_profile_id || latest.tiktok?.gemlogin_profile_id || latest.id,
    },
    youtube: {
      ...(latest.youtube || {}),
      gemlogin_profile_id: draft.youtube?.gemlogin_profile_id || latest.youtube?.gemlogin_profile_id || latest.id,
    },
    facebook: {
      ...(latest.facebook || {}),
      gemlogin_profile_id: draft.facebook?.gemlogin_profile_id || latest.facebook?.gemlogin_profile_id || latest.id,
    },
  };
}

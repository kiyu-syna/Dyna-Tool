import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl, revealItemInDir } from "@tauri-apps/plugin-opener";

type Unlisten = () => void;

function subscribe<T>(event: string, callback: (payload: T) => void): Unlisten {
  let disposed = false;
  let unlisten: Unlisten | undefined;
  void listen<T>(event, ({ payload }) => callback(payload)).then((next) => {
    if (disposed) next();
    else unlisten = next;
  });
  return () => {
    disposed = true;
    unlisten?.();
  };
}

export function installTauriBridge(): void {
  if (!isTauri() || window.dyna) return;

  window.dyna = {
    request: <T>(request: DynaRequest) => invoke<T>("dyna_api", { request }),
    backendInfo: () => invoke<Record<string, unknown>>("backend_info"),
    async selectMedia() {
      const selected = await open({
        title: "Chọn video để đăng",
        multiple: true,
        directory: false,
        filters: [{ name: "Video", extensions: ["mp4", "m4v", "mov", "webm"] }],
      });
      if (!selected) return [];
      return Array.isArray(selected) ? selected : [selected];
    },
    localMediaUrl: (projectId) => invoke<string>("local_media_url", { projectId }),
    localDubbingUrl: (projectId) => invoke<string>("local_dubbing_url", { projectId }),
    localTtsPreviewUrl: (previewId) => invoke<string>("local_tts_preview_url", { previewId }),
    async revealFile(filePath) {
      await revealItemInDir(filePath);
      return { ok: true };
    },
    async openExternal(url) {
      const parsed = new URL(url);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        throw new Error("Chỉ có thể mở liên kết HTTP hoặc HTTPS");
      }
      await openUrl(parsed.toString());
      return { ok: true };
    },
    async openLogWindow() {
      await invoke("open_log_window");
      return { ok: true };
    },
    async windowControl(action) {
      const current = getCurrentWindow();
      if (action === "minimize") await current.minimize();
      else if (action === "maximize") await current.toggleMaximize();
      else if (action === "close") await current.close();
      else if (action === "focus") {
        await current.show();
        await current.setFocus();
      }
    },
    onBackendStatus: (callback) => subscribe("dyna:backend-status", callback),
    onBackendLog: (callback) => subscribe("dyna:backend-log", callback),
    onNavigate: (callback) => subscribe("dyna:navigate", callback),
  };
}

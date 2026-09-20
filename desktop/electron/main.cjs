const { app, BrowserWindow, dialog, ipcMain, Menu, shell } = require("electron");
const { randomBytes } = require("node:crypto");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..", "..");
const apiToken = randomBytes(32).toString("hex");
let backendProcess = null;
let backendInfo = null;
let mainWindow = null;
let logWindow = null;
let quitting = false;
let shutdownStarted = false;
let backendRestartPromise = null;
let backendRestartTimer = null;
let selectionFocusTimer = null;
let lastFocusedSelectionId = "";
const statusWaiters = [];
const isDevelopment = process.argv.includes("--dev");
// The browser extension connects to this fixed loopback bridge. A caller may
// override it for development, but the packaged extension expects port 8765.
const extensionBridgePort = String(process.env.DYNA_EXTENSION_PORT || "8765");

function packagedDataRoot() {
  return app.getPath("userData");
}

function preparePackagedData() {
  if (!app.isPackaged) return;
  fs.mkdirSync(packagedDataRoot(), { recursive: true });
}

function appResource(...parts) {
  return app.isPackaged
    ? path.join(process.resourcesPath, ...parts)
    : path.join(projectRoot, ...parts);
}

function broadcast(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function resolveBackendCommand() {
  if (app.isPackaged) {
    const dataRoot = packagedDataRoot();
    return {
      command: path.join(process.resourcesPath, "backend", "DynaBackend.exe"),
      args: ["--port", extensionBridgePort, "--token", apiToken],
      cwd: dataRoot,
      dataRoot,
      playwrightDriverDir: path.join(process.resourcesPath, "playwright-driver"),
    };
  }

  const configuredPython = process.env.DYNA_PYTHON;
  const venvPython = path.join(projectRoot, ".venv", "Scripts", "python.exe");
  return {
    command: configuredPython || venvPython,
    args: ["-u", "-m", "desktop_backend.api", "--port", extensionBridgePort, "--token", apiToken],
    cwd: projectRoot,
  };
}

function startBackend() {
  if (backendRestartTimer) {
    clearTimeout(backendRestartTimer);
    backendRestartTimer = null;
  }
  if (backendProcess) return waitForBackend();
  if (quitting) return Promise.reject(new Error("Dyna is shutting down"));
  const launch = resolveBackendCommand();
  const child = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONUNBUFFERED: "1",
      PYTHONDONTWRITEBYTECODE: "1",
      ...(launch.dataRoot ? { DYNA_DATA_DIR: launch.dataRoot } : {}),
      ...(launch.playwrightDriverDir ? { DYNA_PLAYWRIGHT_DRIVER_DIR: launch.playwrightDriverDir } : {}),
    },
  });
  backendProcess = child;

  let stdoutBuffer = "";
  child.stdout.on("data", (chunk) => {
    stdoutBuffer += chunk.toString("utf8");
    const lines = stdoutBuffer.split(/\r?\n/);
    stdoutBuffer = lines.pop() || "";
    for (const line of lines) {
      const readyMarker = "DYNA_API_READY ";
      const readyIndex = line.indexOf(readyMarker);
      if (readyIndex !== -1) {
        if (backendProcess !== child) continue;
        backendInfo = JSON.parse(line.slice(readyIndex + readyMarker.length));
        backendInfo.baseUrl = `http://${backendInfo.host}:${backendInfo.port}`;
        for (const resolve of statusWaiters.splice(0)) resolve(backendInfo);
        broadcast("dyna:backend-status", { state: "ready", ...backendInfo });
      } else if (line.trim()) {
        broadcast("dyna:backend-log", line);
      }
    }
  });

  child.stderr.on("data", (chunk) => {
    const message = chunk.toString("utf8").trim();
    if (message) broadcast("dyna:backend-log", message);
  });

  child.on("error", (error) => {
    if (backendProcess !== child) return;
    backendProcess = null;
    backendInfo = null;
    broadcast("dyna:backend-status", { state: "failed", error: String(error) });
    for (const resolve of statusWaiters.splice(0)) resolve(null);
    scheduleBackendRestart();
  });

  child.on("exit", (code) => {
    if (backendProcess !== child) return;
    backendProcess = null;
    backendInfo = null;
    broadcast("dyna:backend-status", { state: quitting ? "stopped" : "failed", code });
    if (!quitting) {
      for (const resolve of statusWaiters.splice(0)) resolve(null);
      scheduleBackendRestart();
    }
  });

  return waitForBackend();
}

function waitForBackend(timeoutMs = 15000) {
  if (backendInfo) return Promise.resolve(backendInfo);
  return new Promise((resolve, reject) => {
    let timer = null;
    const waiter = (info) => {
      clearTimeout(timer);
      if (info) resolve(info);
      else reject(new Error("Python backend stopped before becoming ready"));
    };
    timer = setTimeout(() => {
      const index = statusWaiters.indexOf(waiter);
      if (index >= 0) statusWaiters.splice(index, 1);
      reject(new Error("Python backend startup timed out"));
    }, timeoutMs);
    statusWaiters.push(waiter);
  });
}

function scheduleBackendRestart(delayMs = 700) {
  if (quitting || shutdownStarted || backendRestartTimer || backendRestartPromise) return;
  backendRestartTimer = setTimeout(() => {
    backendRestartTimer = null;
    void restartBackend().catch((error) => {
      broadcast("dyna:backend-status", { state: "failed", error: String(error) });
      scheduleBackendRestart(2000);
    });
  }, delayMs);
}

function restartBackend() {
  if (backendRestartPromise) return backendRestartPromise;
  if (quitting) return Promise.reject(new Error("Dyna is shutting down"));
  backendRestartPromise = (async () => {
    const previous = backendProcess;
    backendProcess = null;
    backendInfo = null;
    if (previous && !previous.killed) previous.kill();
    broadcast("dyna:backend-status", { state: "starting" });
    return startBackend();
  })().finally(() => {
    backendRestartPromise = null;
  });
  return backendRestartPromise;
}

function ensureBackend() {
  if (backendInfo) return Promise.resolve(backendInfo);
  if (backendRestartPromise) return backendRestartPromise;
  if (!backendProcess) return startBackend();
  return waitForBackend();
}

function isBackendConnectionError(error) {
  let current = error;
  while (current) {
    if (["ECONNREFUSED", "ECONNRESET", "EPIPE"].includes(current.code)) return true;
    current = current.cause;
  }
  return false;
}

async function apiRequest(request) {
  const apiPath = String(request?.path || "");
  if (!apiPath.startsWith("/api/")) throw new Error("Invalid API path");
  const method = String(request?.method || "GET").toUpperCase();
  if (!["GET", "POST", "PUT", "DELETE"].includes(method)) throw new Error("Invalid API method");
  const requestedTimeout = Number(request?.timeoutMs || 55_000);
  const timeoutMs = Number.isFinite(requestedTimeout)
    ? Math.min(10 * 60_000, Math.max(5_000, requestedTimeout))
    : 55_000;

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const info = await ensureBackend();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${info.baseUrl}${apiPath}`, {
        method,
        signal: controller.signal,
        headers: {
          "X-Dyna-Token": apiToken,
          ...(request?.body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: request?.body === undefined ? undefined : JSON.stringify(request.body),
      });
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = { detail: text || `HTTP ${response.status}` };
      }
      if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
      return data;
    } catch (error) {
      if (error?.name === "AbortError") {
        throw new Error(
          `Yêu cầu Dyna quá thời gian chờ (${Math.ceil(timeoutMs / 1000)} giây)`,
          { cause: error },
        );
      }
      if (attempt === 0 && isBackendConnectionError(error)) {
        await restartBackend();
        continue;
      }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
  throw new Error("Python backend is unavailable");
}

function createWindow() {
  Menu.setApplicationMenu(null);
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 900,
    minWidth: 1120,
    minHeight: 700,
    backgroundColor: "#161a21",
    frame: false,
    title: "Dyna",
    icon: appResource("image", "dyna-app-icon.ico"),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  if (isDevelopment) {
    mainWindow.loadURL("http://127.0.0.1:5174");
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

function focusMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function startSelectionFocusWatcher() {
  if (selectionFocusTimer) return;
  selectionFocusTimer = setInterval(() => {
    void apiRequest({ path: "/api/publisher/douyin-selections/active" })
      .then((result) => {
        const session = result?.session;
        const sessionId = String(session?.id || "");
        if (
          sessionId
          && session?.status === "ready"
          && session?.focus_requested
          && sessionId !== lastFocusedSelectionId
        ) {
          lastFocusedSelectionId = sessionId;
          focusMainWindow();
          mainWindow?.webContents.send("dyna:navigate", "publish");
        }
      })
      .catch(() => undefined);
  }, 1000);
}

function createLogWindow() {
  if (logWindow && !logWindow.isDestroyed()) {
    if (logWindow.isMinimized()) logWindow.restore();
    logWindow.show();
    logWindow.focus();
    return;
  }
  logWindow = new BrowserWindow({
    width: 1120,
    height: 720,
    minWidth: 760,
    minHeight: 480,
    backgroundColor: "#0b0f14",
    title: "Dyna Logs",
    titleBarStyle: "hidden",
    titleBarOverlay: {
      color: "#2e2e2e",
      symbolColor: "#c9d5e3",
      height: 32,
    },
    icon: appResource("image", "dyna-app-icon.ico"),
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  logWindow.once("ready-to-show", () => logWindow?.show());
  logWindow.on("closed", () => { logWindow = null; });
  logWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
    if (!logWindow || logWindow.isDestroyed() || String(validatedURL || "").startsWith("data:")) return;
    const detail = `Không thể tải cửa sổ Nhật ký (${errorCode}): ${errorDescription}`;
    const html = `<!doctype html><html lang="vi"><meta charset="utf-8"><body style="margin:0;padding:28px;color:#d7e0ea;background:#090d12;font:14px/1.55 Segoe UI,sans-serif"><h2 style="margin-top:0;color:#ff7d88">Dyna Logs không thể khởi động</h2><p>${detail}</p><p>Hãy đóng cửa sổ này và mở Nhật ký lại. Lỗi đã được ghi vào tiến trình chính của Dyna.</p></body></html>`;
    void logWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  });
  if (isDevelopment) {
    logWindow.loadURL("http://127.0.0.1:5174/logs.html");
  } else {
    logWindow.loadFile(path.join(__dirname, "..", "dist", "logs.html"));
  }
}

function stopBackend() {
  if (selectionFocusTimer) {
    clearInterval(selectionFocusTimer);
    selectionFocusTimer = null;
  }
  if (backendRestartTimer) {
    clearTimeout(backendRestartTimer);
    backendRestartTimer = null;
  }
  if (!backendProcess) return;
  const child = backendProcess;
  backendProcess = null;
  backendInfo = null;
  child.kill();
}

async function shutdownApplication(event) {
  if (shutdownStarted) return;
  event.preventDefault();
  shutdownStarted = true;
  quitting = true;
  try {
    if (backendInfo) {
      await Promise.race([
        apiRequest({ path: "/api/runtime/shutdown", method: "POST" }),
        new Promise((resolve) => setTimeout(resolve, 10000)),
      ]);
    }
  } catch {
    // The backend may already be unavailable; process cleanup still runs below.
  }
  stopBackend();
  app.quit();
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    void ensureBackend().catch(() => scheduleBackendRestart());
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    preparePackagedData();
    ipcMain.handle("dyna:api", (_event, request) => apiRequest(request));
    ipcMain.handle("dyna:backend-info", () => ({
      state: backendInfo ? "ready" : "starting",
      ...(backendInfo || {}),
    }));
    ipcMain.handle("dyna:select-media", async () => {
      const options = {
        title: "Chọn video để đăng",
        properties: ["openFile", "multiSelections"],
        filters: [
          { name: "Video", extensions: ["mp4", "m4v", "mov", "webm"] },
        ],
      };
      const result = mainWindow && !mainWindow.isDestroyed()
        ? await dialog.showOpenDialog(mainWindow, options)
        : await dialog.showOpenDialog(options);
      return result.canceled ? [] : result.filePaths;
    });
    ipcMain.handle("dyna:local-media-url", async (_event, value) => {
      const projectId = String(value || "").trim().toLowerCase();
      if (!/^[a-f0-9]{32}$/.test(projectId)) {
        throw new Error("Mã dự án video xem trước không hợp lệ");
      }
      const info = await ensureBackend();
      return `${info.baseUrl}/api/video-ai/projects/${projectId}/media?access_token=${encodeURIComponent(apiToken)}`;
    });
    ipcMain.handle("dyna:local-dubbing-url", async (_event, value) => {
      const projectId = String(value || "").trim().toLowerCase();
      if (!/^[a-f0-9]{32}$/.test(projectId)) {
        throw new Error("Mã dự án lồng tiếng không hợp lệ");
      }
      const info = await ensureBackend();
      return `${info.baseUrl}/api/video-ai/projects/${projectId}/dubbing-audio?access_token=${encodeURIComponent(apiToken)}`;
    });
    ipcMain.handle("dyna:local-tts-preview-url", async (_event, value) => {
      const previewId = String(value || "").trim().toLowerCase();
      if (!/^[a-f0-9]{32}$/.test(previewId)) {
        throw new Error("Mã bản nghe thử giọng đọc không hợp lệ");
      }
      const info = await ensureBackend();
      return `${info.baseUrl}/api/video-ai/tts/previews/${previewId}?access_token=${encodeURIComponent(apiToken)}`;
    });
    ipcMain.handle("dyna:reveal-file", (_event, value) => {
      const target = path.resolve(String(value || ""));
      if (!path.isAbsolute(target) || !fs.existsSync(target)) {
        throw new Error("Không tìm thấy file đầu ra");
      }
      shell.showItemInFolder(target);
      return { ok: true };
    });
    ipcMain.handle("dyna:open-external", async (_event, value) => {
      const target = String(value || "").trim();
      let parsed;
      try {
        parsed = new URL(target);
      } catch {
        throw new Error("Liên kết video không hợp lệ");
      }
      if (!["http:", "https:"].includes(parsed.protocol)) {
        throw new Error("Chỉ có thể mở liên kết HTTP hoặc HTTPS");
      }
      await shell.openExternal(parsed.toString());
      return { ok: true };
    });
    ipcMain.handle("dyna:open-log-window", () => {
      createLogWindow();
      return { ok: true };
    });
    ipcMain.handle("dyna:window-control", (_event, action) => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      if (action === "minimize") mainWindow.minimize();
      else if (action === "maximize") mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
      else if (action === "close") mainWindow.close();
      else if (action === "focus") {
        focusMainWindow();
      }
    });
    try {
      await startBackend();
      createWindow();
      startSelectionFocusWatcher();
    } catch (error) {
      createWindow();
      startSelectionFocusWatcher();
      broadcast("dyna:backend-status", { state: "failed", error: String(error) });
    }
  });

  app.on("before-quit", (event) => void shutdownApplication(event));
  app.on("window-all-closed", () => app.quit());
}

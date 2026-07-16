const { app, BrowserWindow, ipcMain, Menu } = require("electron");
const { randomBytes } = require("node:crypto");
const { spawn } = require("node:child_process");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..", "..");
const apiToken = randomBytes(32).toString("hex");
let backendProcess = null;
let backendInfo = null;
let mainWindow = null;
let quitting = false;
let shutdownStarted = false;
let backendRestartPromise = null;
let backendRestartTimer = null;
const statusWaiters = [];
const isDevelopment = process.argv.includes("--dev");
const extensionBridgePort = String(process.env.DYNA_EXTENSION_PORT || "8765");

function broadcast(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function resolveBackendCommand() {
  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, "backend", "DynaBackend.exe"),
      args: ["--port", extensionBridgePort, "--token", apiToken],
      cwd: path.dirname(process.execPath),
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
    },
  });
  backendProcess = child;

  let stdoutBuffer = "";
  child.stdout.on("data", (chunk) => {
    stdoutBuffer += chunk.toString("utf8");
    const lines = stdoutBuffer.split(/\r?\n/);
    stdoutBuffer = lines.pop() || "";
    for (const line of lines) {
      if (line.startsWith("DYNA_API_READY ")) {
        if (backendProcess !== child) continue;
        backendInfo = JSON.parse(line.slice("DYNA_API_READY ".length));
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

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const info = await ensureBackend();
    try {
      const response = await fetch(`${info.baseUrl}${apiPath}`, {
        method,
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
      if (attempt === 0 && isBackendConnectionError(error)) {
        await restartBackend();
        continue;
      }
      throw error;
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
    backgroundColor: "#f3f6fa",
    title: "Dyna",
    icon: path.join(projectRoot, "image", "dyna-app-icon.ico"),
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
    mainWindow.loadURL("http://127.0.0.1:5173");
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

function stopBackend() {
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
    ipcMain.handle("dyna:api", (_event, request) => apiRequest(request));
    ipcMain.handle("dyna:backend-info", () => ({
      state: backendInfo ? "ready" : "starting",
      ...(backendInfo || {}),
    }));
    try {
      await startBackend();
      createWindow();
    } catch (error) {
      createWindow();
      broadcast("dyna:backend-status", { state: "failed", error: String(error) });
    }
  });

  app.on("before-quit", (event) => void shutdownApplication(event));
  app.on("window-all-closed", () => app.quit());
}

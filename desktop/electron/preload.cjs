const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("dyna", {
  request: (request) => ipcRenderer.invoke("dyna:api", request),
  backendInfo: () => ipcRenderer.invoke("dyna:backend-info"),
  selectMedia: () => ipcRenderer.invoke("dyna:select-media"),
  localMediaUrl: (projectId) => ipcRenderer.invoke("dyna:local-media-url", projectId),
  localDubbingUrl: (projectId) => ipcRenderer.invoke("dyna:local-dubbing-url", projectId),
  localTtsPreviewUrl: (previewId) => ipcRenderer.invoke("dyna:local-tts-preview-url", previewId),
  revealFile: (filePath) => ipcRenderer.invoke("dyna:reveal-file", filePath),
  openExternal: (url) => ipcRenderer.invoke("dyna:open-external", url),
  openLogWindow: () => ipcRenderer.invoke("dyna:open-log-window"),
  windowControl: (action) => ipcRenderer.invoke("dyna:window-control", action),
  onBackendStatus: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("dyna:backend-status", listener);
    return () => ipcRenderer.removeListener("dyna:backend-status", listener);
  },
  onBackendLog: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("dyna:backend-log", listener);
    return () => ipcRenderer.removeListener("dyna:backend-log", listener);
  },
  onNavigate: (callback) => {
    const listener = (_event, page) => callback(page);
    ipcRenderer.on("dyna:navigate", listener);
    return () => ipcRenderer.removeListener("dyna:navigate", listener);
  },
});

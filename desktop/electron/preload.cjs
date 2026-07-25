const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("dyna", {
  request: (request) => ipcRenderer.invoke("dyna:api", request),
  backendInfo: () => ipcRenderer.invoke("dyna:backend-info"),
  selectMedia: () => ipcRenderer.invoke("dyna:select-media"),
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
});

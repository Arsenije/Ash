"use strict";

const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("api", {
  getConfig: () => ipcRenderer.invoke("get-config"),
  engineStatus: () => ipcRenderer.invoke("engine-status"),
  // Configure + start the stack on the chosen model (id from getConfig().models).
  engineSet: (choiceId) => ipcRenderer.invoke("engine-set", choiceId),
  // Download the chosen model's GGUFs; progress arrives via onModelProgress until it resolves.
  downloadModels: (choiceId) => ipcRenderer.invoke("download-models", choiceId),
  onModelProgress: (cb) => {
    const handler = (_e, p) => cb(p);
    ipcRenderer.on("model-download-progress", handler);
    return () => ipcRenderer.removeListener("model-download-progress", handler);
  },
  pickPaths: () => ipcRenderer.invoke("pick-paths"),
  reveal: (p) => ipcRenderer.invoke("reveal", p),
  // Immich connection settings (persisted in main; API key encrypted at rest).
  immichGet: () => ipcRenderer.invoke("immich-get"),
  immichSave: (cfg) => ipcRenderer.invoke("immich-save", cfg),
  immichClear: () => ipcRenderer.invoke("immich-clear"),
  // Resolve absolute filesystem paths for dragged-in File objects.
  // file.path was removed in Electron 32+; webUtils.getPathForFile is the replacement.
  pathForFile: (file) => webUtils.getPathForFile(file),
});

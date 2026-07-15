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
  // Immich connection (persisted in main; the API key is encrypted at rest and
  // never handed to this renderer — immichGet reports only hasKey, and the
  // immich calls are proxied through main, which injects the stored key).
  immichGet: () => ipcRenderer.invoke("immich-get"),
  immichTest: (cfg) => ipcRenderer.invoke("immich-test", cfg),
  immichAlbums: (cfg) => ipcRenderer.invoke("immich-albums", cfg),
  immichImport: (payload) => ipcRenderer.invoke("immich-import", payload),
  immichClear: () => ipcRenderer.invoke("immich-clear"),
  // Resolve absolute filesystem paths for dragged-in File objects.
  // file.path was removed in Electron 32+; webUtils.getPathForFile is the replacement.
  pathForFile: (file) => webUtils.getPathForFile(file),
});

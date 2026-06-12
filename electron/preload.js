"use strict";

const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("api", {
  getConfig: () => ipcRenderer.invoke("get-config"),
  saveKey: (key) => ipcRenderer.invoke("save-key", key),
  pickPaths: () => ipcRenderer.invoke("pick-paths"),
  reveal: (p) => ipcRenderer.invoke("reveal", p),
  // Resolve absolute filesystem paths for dragged-in File objects.
  // file.path was removed in Electron 32+; webUtils.getPathForFile is the replacement.
  pathForFile: (file) => webUtils.getPathForFile(file),
});

"use strict";

const { app, BrowserWindow, ipcMain, dialog, shell, safeStorage } = require("electron");
const { spawn } = require("node:child_process");

app.setName("Ash");
const net = require("node:net");
const path = require("node:path");
const fs = require("node:fs");

const ROOT = path.join(__dirname, "..");
const SIDECAR_DIR = path.join(ROOT, "sidecar");
const PY = path.join(SIDECAR_DIR, ".venv", "bin", "python");

let mainWindow = null;
let sidecar = null;
let sidecarPort = 0;
let dataDir = "";
let settingsPath = "";

// ---------------------------------------------------------------------------
// Settings (OpenAI key) — encrypted at rest via safeStorage when available.
// ---------------------------------------------------------------------------
function loadKey() {
  try {
    const raw = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
    if (!raw.openai_key) return "";
    if (raw.encrypted && safeStorage.isEncryptionAvailable()) {
      return safeStorage.decryptString(Buffer.from(raw.openai_key, "base64"));
    }
    return raw.encrypted ? "" : raw.openai_key;
  } catch {
    return "";
  }
}

function saveKey(key) {
  const useEnc = safeStorage.isEncryptionAvailable();
  const payload = useEnc
    ? { encrypted: true, openai_key: safeStorage.encryptString(key).toString("base64") }
    : { encrypted: false, openai_key: key };
  fs.writeFileSync(settingsPath, JSON.stringify(payload), { mode: 0o600 });
}

// ---------------------------------------------------------------------------
// Sidecar lifecycle
// ---------------------------------------------------------------------------
function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function waitForHealth(port, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function stopSidecar() {
  if (sidecar) {
    sidecar.kill();
    sidecar = null;
  }
}

async function startSidecar() {
  stopSidecar();
  sidecarPort = await freePort();
  const env = {
    ...process.env,
    KHORA_PHOTO_DATA_DIR: dataDir,
    PHOTO_SIDECAR_PORT: String(sidecarPort),
    PYTHONUNBUFFERED: "1",
  };
  const key = loadKey();
  if (key) env.OPENAI_API_KEY = key;

  sidecar = spawn(
    PY,
    ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", String(sidecarPort), "--log-level", "warning"],
    { cwd: SIDECAR_DIR, env, stdio: ["ignore", "pipe", "pipe"] }
  );
  sidecar.stdout.on("data", (d) => process.stdout.write(`[sidecar] ${d}`));
  sidecar.stderr.on("data", (d) => process.stderr.write(`[sidecar] ${d}`));
  sidecar.on("exit", (code) => console.log(`[sidecar] exited ${code}`));

  const ok = await waitForHealth(sidecarPort);
  if (!ok) throw new Error("sidecar failed to become healthy");
  return sidecarPort;
}

// ---------------------------------------------------------------------------
// IPC
// ---------------------------------------------------------------------------
ipcMain.handle("get-config", async () => ({
  baseUrl: `http://127.0.0.1:${sidecarPort}`,
  hasKey: Boolean(loadKey()),
}));

ipcMain.handle("save-key", async (_e, key) => {
  saveKey((key || "").trim());
  await startSidecar(); // relaunch so the sidecar picks up the new key
  return { ok: true, baseUrl: `http://127.0.0.1:${sidecarPort}`, hasKey: Boolean(loadKey()) };
});

ipcMain.handle("pick-paths", async () => {
  const res = await dialog.showOpenDialog(mainWindow, {
    title: "Add photos or a folder",
    properties: ["openFile", "openDirectory", "multiSelections"],
  });
  return res.canceled ? [] : res.filePaths;
});

ipcMain.handle("reveal", async (_e, p) => {
  if (p) shell.showItemInFolder(p);
});

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    title: "Ash",
    backgroundColor: "#0f1115",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(ROOT, "renderer", "index.html"));
}

app.whenReady().then(async () => {
  dataDir = path.join(app.getPath("userData"), "khora-data");
  fs.mkdirSync(dataDir, { recursive: true });
  settingsPath = path.join(app.getPath("userData"), "settings.json");

  try {
    await startSidecar();
  } catch (err) {
    console.error("sidecar startup failed:", err);
  }
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", stopSidecar);
app.on("window-all-closed", () => app.quit());

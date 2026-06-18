"use strict";

const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const { spawn } = require("node:child_process");

app.setName("Ash");
const crypto = require("node:crypto");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const fs = require("node:fs");

const ROOT = path.join(__dirname, "..");
const SIDECAR_DIR = path.join(ROOT, "sidecar");
const IS_WIN = process.platform === "win32";
const PY = IS_WIN
  ? path.join(SIDECAR_DIR, ".venv", "Scripts", "python.exe")
  : path.join(SIDECAR_DIR, ".venv", "bin", "python");
const ICON = path.join(ROOT, "assets", "icon.png"); // drop a 1024×1024 PNG here

let mainWindow = null;
let sidecar = null;
let sidecarPort = 0;
let sidecarToken = ""; // per-session shared secret guarding the sidecar HTTP API
let llamaSwap = null;
let swapPort = 0;
let dataDir = "";
let modelsDir = "";
let swapConfigPath = "";
let settingsPath = "";

// ---------------------------------------------------------------------------
// Engine config — the models that describe photos and power search.
// Ash is local-only: GGUF models run on this machine via llama.cpp, fronted by
// llama-swap (one OpenAI-compatible endpoint, swaps models on demand). The
// chosen engine is persisted in settings.json under "engine".
// ---------------------------------------------------------------------------

// llama-swap model ids (what the sidecar/khora address) → how to fetch + serve.
//   role "main" is the GGUF; vision also needs an "mmproj" projector GGUF.
// repo/quant feed a HuggingFace API lookup so we don't hard-code filenames.
const MODELS = {
  vision: { label: "SmolVLM2 2.2B", repo: "ggml-org/SmolVLM2-2.2B-Instruct-GGUF", quant: "Q4_K_M", mmproj: true },
  embed: { label: "Nomic Embed", repo: "nomic-ai/nomic-embed-text-v1.5-GGUF", quant: "Q4_K_M" },
  // Qwen2.5-3B (not a 1B) — entity extraction needs a model that emits a single
  // clean JSON object; 1B models wrap it in prose/fences and khora drops it.
  text: { label: "Qwen2.5 3B", repo: "bartowski/Qwen2.5-3B-Instruct-GGUF", quant: "Q4_K_M" },
};

// The engine record persisted to settings. base_url is resolved at runtime from
// the live llama-swap port, so it isn't stored here.
const LOCAL_ENGINE = {
  provider: "local",
  vision_model: "vision",
  text_model: "text",
  embed_model: "embed",
  embed_dim: 768, // nomic-embed-text-v1.5
};

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsPath, "utf8"));
  } catch {
    return {};
  }
}

function writeSettings(obj) {
  fs.writeFileSync(settingsPath, JSON.stringify(obj), { mode: 0o600 });
}

// The configured engine, or null when the user hasn't set up yet (first run).
function loadEngine() {
  return readSettings().engine || null;
}

function saveEngine(engine) {
  const s = readSettings();
  s.engine = engine;
  writeSettings(s);
}

// Environment the sidecar needs to reach llama-swap. vision.py's OpenAI SDK
// reads OPENAI_BASE_URL; khora's litellm needs the openai/ prefix + OPENAI_API_BASE.
// Both point at llama-swap, which routes by the model name.
function engineEnv(engine) {
  if (!engine) return {};
  const base = `http://127.0.0.1:${swapPort}/v1`;
  return {
    PHOTO_VISION_MODEL: engine.vision_model,
    OPENAI_BASE_URL: base,
    OPENAI_API_BASE: base,
    OPENAI_API_KEY: "sk-local", // dummy; llama.cpp ignores it
    KHORA_LLM_MODEL: `openai/${engine.text_model}`,
    KHORA_EXTRACTION_MODEL: `openai/${engine.text_model}`,
    KHORA_EMBED_MODEL: `openai/${engine.embed_model}`,
    KHORA_EMBED_DIM: String(engine.embed_dim),
  };
}

// Coarse hardware tier for the onboarding warning.
//   macOS: Apple Silicon runs on the GPU (Metal); an Intel Mac is CPU-only (slow).
//   Windows/Linux: we can't cheaply tell if there's a usable GPU, so we only
//   warn on low memory (the "slow" Intel-Mac copy never fires off-Mac).
function hardwareTier() {
  const totalRamGB = Math.round(os.totalmem() / 1e9);
  if (process.platform === "darwin" && !(os.cpus()[0]?.model || "").includes("Apple")) {
    return { tier: "slow", totalRamGB };
  }
  if (totalRamGB < 8) return { tier: "lowram", totalRamGB };
  return { tier: "good", totalRamGB };
}

// ---------------------------------------------------------------------------
// llama.cpp binaries — bundled in the app (Resources/bin), with dev fallbacks.
// ---------------------------------------------------------------------------
function findBin(name) {
  const exe = IS_WIN ? `${name}.exe` : name;
  const candidates = [
    process.resourcesPath && path.join(process.resourcesPath, "bin", exe), // bundled
    path.join(ROOT, "vendor", "bin", exe), // fetched by scripts/fetch-binaries
    process.platform === "darwin" && `/opt/homebrew/bin/${exe}`,
    process.platform === "darwin" && `/usr/local/bin/${exe}`,
    process.platform === "linux" && `/usr/local/bin/${exe}`,
    process.platform === "linux" && `/usr/bin/${exe}`,
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p)) || null;
}

function runtimeAvailable() {
  return Boolean(findBin("llama-server") && findBin("llama-swap"));
}

// ---------------------------------------------------------------------------
// Model files — local paths + HuggingFace download with progress.
// ---------------------------------------------------------------------------
function modelPath(key, role) {
  return path.join(modelsDir, role === "mmproj" ? `${key}-mmproj.gguf` : `${key}.gguf`);
}

function modelsPresent() {
  return Object.entries(MODELS).every(
    ([key, m]) => fs.existsSync(modelPath(key, "main")) && (!m.mmproj || fs.existsSync(modelPath(key, "mmproj")))
  );
}

// List a repo's files via the HF API, then pick the GGUF (or its mmproj).
async function resolveHfFile(repo, quant, { mmproj = false } = {}) {
  const res = await fetch(`https://huggingface.co/api/models/${repo}`);
  if (!res.ok) throw new Error(`HuggingFace lookup failed for ${repo} (${res.status})`);
  const data = await res.json();
  const ggufs = (data.siblings || []).map((s) => s.rfilename).filter((f) => f.toLowerCase().endsWith(".gguf"));
  if (mmproj) return ggufs.find((f) => f.toLowerCase().includes("mmproj")) || null;
  const q = quant.toLowerCase();
  return (
    ggufs.find((f) => f.toLowerCase().includes(q) && !f.toLowerCase().includes("mmproj")) ||
    ggufs.find((f) => !f.toLowerCase().includes("mmproj")) ||
    null
  );
}

function hfUrl(repo, file) {
  return `https://huggingface.co/${repo}/resolve/main/${file}`;
}

// Stream a download to disk (with backpressure), reporting fraction complete.
// Writes to a .part file and only promotes it to `dest` once the full
// content-length has arrived, so a dropped/truncated connection can't leave a
// corrupt model that later looks valid. Cleans up the .part on any failure.
async function downloadTo(url, dest, onProgress) {
  const res = await fetch(url);
  if (!res.ok || !res.body) throw new Error(`download failed (${res.status})`);
  const total = Number(res.headers.get("content-length")) || 0;
  const tmp = `${dest}.part`;
  const out = fs.createWriteStream(tmp);
  let received = 0;
  try {
    for await (const chunk of res.body) {
      received += chunk.length;
      if (!out.write(Buffer.from(chunk))) await new Promise((r) => out.once("drain", r));
      if (total) onProgress(received / total);
    }
    await new Promise((resolve, reject) => out.end((err) => (err ? reject(err) : resolve())));
  } catch (err) {
    out.destroy();
    fs.rmSync(tmp, { force: true });
    throw err;
  }
  if (total && received !== total) {
    fs.rmSync(tmp, { force: true });
    throw new Error(`download incomplete: got ${received} of ${total} bytes`);
  }
  fs.renameSync(tmp, dest);
}

// Download every model (skipping ones already on disk), reporting progress.
async function downloadModels(onProgress) {
  fs.mkdirSync(modelsDir, { recursive: true });
  for (const [key, m] of Object.entries(MODELS)) {
    const jobs = [{ role: "main", label: m.label }];
    if (m.mmproj) jobs.push({ role: "mmproj", label: `${m.label} (vision projector)` });
    for (const job of jobs) {
      const dest = modelPath(key, job.role);
      if (fs.existsSync(dest) && fs.statSync(dest).size > 0) continue; // already have it
      const file = await resolveHfFile(m.repo, m.quant, { mmproj: job.role === "mmproj" });
      if (!file) throw new Error(`no ${job.role} GGUF found in ${m.repo}`);
      onProgress({ model: job.label, fraction: 0 });
      await downloadTo(hfUrl(m.repo, file), dest, (f) => onProgress({ model: job.label, fraction: f }));
      onProgress({ model: job.label, fraction: 1 });
    }
  }
}

// ---------------------------------------------------------------------------
// llama-swap — one OpenAI-compatible endpoint fronting per-model llama-server.
// ---------------------------------------------------------------------------
function writeSwapConfig() {
  const server = findBin("llama-server");
  const q = (p) => `"${p}"`;
  // cmd is a YAML block scalar (|) so the quoted paths aren't parsed as YAML.
  // ${PORT} is llama-swap's macro (assigned per backend) — keep it literal.
  const cfg = `models:
  vision:
    ttl: 300
    cmd: |
      ${q(server)} --host 127.0.0.1 --port \${PORT} -m ${q(modelPath("vision", "main"))} --mmproj ${q(modelPath("vision", "mmproj"))}
  embed:
    ttl: 300
    cmd: |
      ${q(server)} --host 127.0.0.1 --port \${PORT} -m ${q(modelPath("embed", "main"))} --embedding
  text:
    ttl: 300
    cmd: |
      ${q(server)} --host 127.0.0.1 --port \${PORT} --jinja -m ${q(modelPath("text", "main"))}
`;
  fs.writeFileSync(swapConfigPath, cfg);
}

function stopSwap() {
  if (llamaSwap) {
    llamaSwap.kill();
    llamaSwap = null;
  }
}

async function startSwap() {
  stopSwap();
  const swap = findBin("llama-swap");
  if (!swap || !findBin("llama-server")) throw new Error("llama.cpp runtime not found");
  swapPort = await freePort();
  writeSwapConfig();
  llamaSwap = spawn(swap, ["--config", swapConfigPath, "--listen", `127.0.0.1:${swapPort}`], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  llamaSwap.stdout.on("data", (d) => process.stdout.write(`[swap] ${d}`));
  llamaSwap.stderr.on("data", (d) => process.stderr.write(`[swap] ${d}`));
  llamaSwap.on("exit", (code) => console.log(`[swap] exited ${code}`));
  // /v1/models responds as soon as the proxy is up (no model loaded yet).
  const ok = await waitForUrl(`http://127.0.0.1:${swapPort}/v1/models`);
  if (!ok) throw new Error("llama-swap failed to start");
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

async function waitForHealth(port, token, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  const headers = token ? { "X-Ash-Token": token } : undefined;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`, { headers });
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

async function waitForUrl(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 400));
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
  const engine = loadEngine();
  if (!engine) return 0; // nothing to run until the user picks an engine
  sidecarPort = await freePort();
  sidecarToken = crypto.randomBytes(32).toString("hex"); // fresh per launch
  const env = {
    ...process.env,
    KHORA_PHOTO_DATA_DIR: dataDir,
    PHOTO_SIDECAR_PORT: String(sidecarPort),
    PHOTO_SIDECAR_TOKEN: sidecarToken,
    PYTHONUNBUFFERED: "1",
    ...engineEnv(engine),
  };

  sidecar = spawn(
    PY,
    ["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", String(sidecarPort), "--log-level", "warning"],
    { cwd: SIDECAR_DIR, env, stdio: ["ignore", "pipe", "pipe"] }
  );
  sidecar.stdout.on("data", (d) => process.stdout.write(`[sidecar] ${d}`));
  sidecar.stderr.on("data", (d) => process.stderr.write(`[sidecar] ${d}`));
  sidecar.on("exit", (code) => console.log(`[sidecar] exited ${code}`));

  const ok = await waitForHealth(sidecarPort, sidecarToken);
  if (!ok) throw new Error("sidecar failed to become healthy");
  return sidecarPort;
}

// Bring up the full local stack: llama-swap (model server) then the sidecar.
async function startServers() {
  if (!loadEngine()) return; // nothing to run until the user sets up
  await startSwap();
  await startSidecar();
}

// ---------------------------------------------------------------------------
// IPC
// ---------------------------------------------------------------------------
async function engineStatus() {
  const engine = loadEngine();
  return {
    configured: Boolean(engine),
    baseUrl: sidecarPort ? `http://127.0.0.1:${sidecarPort}` : "",
    token: sidecarToken,
    ready: Boolean(engine) && sidecarPort > 0,
    runtime: { available: runtimeAvailable(), modelsReady: modelsPresent() },
    hardware: hardwareTier(),
    version: app.getVersion(),
  };
}

ipcMain.handle("get-config", async () => engineStatus());
ipcMain.handle("engine-status", async () => engineStatus());

// Set up the local engine and bring the stack up on it.
ipcMain.handle("engine-set", async () => {
  saveEngine({ ...LOCAL_ENGINE });
  try {
    await startServers();
  } catch (err) {
    return { ok: false, error: String(err.message || err), ...(await engineStatus()) };
  }
  return { ok: true, ...(await engineStatus()) };
});

// Download the model GGUFs from HuggingFace, forwarding progress to the renderer.
ipcMain.handle("download-models", async (e) => {
  const send = (p) => e.sender.send("model-download-progress", p);
  try {
    await downloadModels(send);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err.message || err) };
  }
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
    icon: fs.existsSync(ICON) ? ICON : undefined, // win/linux; macOS uses the dock/bundle icon
    backgroundColor: "#000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile(path.join(ROOT, "renderer", "index.html"));

  // Open target="_blank" links (the ↗ source links) in the system browser,
  // not a new Electron window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
}

app.whenReady().then(async () => {
  const userData = app.getPath("userData");
  dataDir = path.join(userData, "khora-data");
  modelsDir = path.join(userData, "models");
  swapConfigPath = path.join(userData, "llama-swap.yaml");
  settingsPath = path.join(userData, "settings.json");
  fs.mkdirSync(dataDir, { recursive: true });

  app.setAboutPanelOptions({ applicationName: "Ash", applicationVersion: app.getVersion() });
  if (process.platform === "darwin" && app.dock && fs.existsSync(ICON)) app.dock.setIcon(ICON);

  try {
    await startServers(); // no-op until an engine is configured
  } catch (err) {
    console.error("startup failed:", err);
  }
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", () => {
  stopSidecar();
  stopSwap();
});
app.on("window-all-closed", () => app.quit());

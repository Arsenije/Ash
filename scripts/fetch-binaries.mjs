#!/usr/bin/env node
// Cross-platform: download the right llama.cpp (llama-server + libs) and
// llama-swap binaries for this OS/arch into vendor/bin. Idempotent.
// Extraction uses `tar`, which handles .tar.gz and .zip on macOS, Linux, and
// Windows 10+ (bsdtar). Run via `npm run binaries`.

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BIN = path.join(ROOT, "vendor", "bin");
const isWin = process.platform === "win32";
const exe = (n) => (isWin ? `${n}.exe` : n);

if (fs.existsSync(path.join(BIN, exe("llama-server"))) && fs.existsSync(path.join(BIN, exe("llama-swap")))) {
  console.log("vendor/bin already populated — skipping.");
  process.exit(0);
}
fs.mkdirSync(BIN, { recursive: true });

const arch = process.arch === "arm64" ? "arm64" : "x64"; // x64 == amd64

// llama.cpp release asset for this platform (CPU/Metal builds — broadest compat).
const LCPP_ASSET = {
  "darwin-arm64": (t) => `llama-${t}-bin-macos-arm64.tar.gz`,
  "darwin-x64": (t) => `llama-${t}-bin-macos-x64.tar.gz`,
  "linux-x64": (t) => `llama-${t}-bin-ubuntu-x64.tar.gz`,
  "linux-arm64": (t) => `llama-${t}-bin-ubuntu-arm64.tar.gz`,
  "win32-x64": (t) => `llama-${t}-bin-win-cpu-x64.zip`,
  "win32-arm64": (t) => `llama-${t}-bin-win-cpu-arm64.zip`,
}[`${process.platform}-${arch}`];

if (!LCPP_ASSET) {
  console.error(`Unsupported platform/arch: ${process.platform}-${arch}`);
  process.exit(1);
}

async function ghLatest(repo) {
  const r = await fetch(`https://api.github.com/repos/${repo}/releases/latest`);
  if (!r.ok) throw new Error(`GitHub API ${repo} -> ${r.status}`);
  return r.json();
}

async function download(url, dest) {
  const r = await fetch(url);
  if (!r.ok || !r.body) throw new Error(`download ${url} -> ${r.status}`);
  const out = fs.createWriteStream(dest);
  for await (const chunk of r.body) {
    if (!out.write(Buffer.from(chunk))) await new Promise((res) => out.once("drain", res));
  }
  await new Promise((res, rej) => out.end((e) => (e ? rej(e) : res())));
}

function extract(archive, dir) {
  fs.mkdirSync(dir, { recursive: true });
  execFileSync("tar", ["-xf", archive, "-C", dir], { stdio: "inherit" });
}

// Recursively find a file by name under dir.
function findFile(dir, name) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      const hit = findFile(p, name);
      if (hit) return hit;
    } else if (e.name === name) {
      return p;
    }
  }
  return null;
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ash-bin-"));
try {
  // --- llama.cpp ---
  const lcpp = await ghLatest("ggml-org/llama.cpp");
  const asset = LCPP_ASSET(lcpp.tag_name);
  console.log(`Fetching ${asset}…`);
  const lcppArchive = path.join(tmp, asset);
  await download(`https://github.com/ggml-org/llama.cpp/releases/download/${lcpp.tag_name}/${asset}`, lcppArchive);
  const lcppDir = path.join(tmp, "llamacpp");
  extract(lcppArchive, lcppDir);
  const server = findFile(lcppDir, exe("llama-server"));
  if (!server) throw new Error("llama-server not found in archive");
  // copy the binary plus its sibling shared libs (.dylib/.so/.dll)
  const serverDir = path.dirname(server);
  for (const f of fs.readdirSync(serverDir)) {
    if (f === exe("llama-server") || /\.(dylib|so|so\.\d+|dll)$/i.test(f) || /\.so(\.|$)/.test(f)) {
      fs.copyFileSync(path.join(serverDir, f), path.join(BIN, f));
    }
  }

  // --- llama-swap ---
  const swapOs = { darwin: "darwin", linux: "linux", win32: "windows" }[process.platform];
  const swapArch = arch === "x64" ? "amd64" : "arm64";
  const swap = await ghLatest("mostlygeek/llama-swap");
  const swapAsset = swap.assets.find(
    (a) => a.name.toLowerCase().includes(swapOs) && a.name.toLowerCase().includes(swapArch)
  );
  if (!swapAsset) throw new Error(`no llama-swap asset for ${swapOs}/${swapArch}`);
  console.log(`Fetching ${swapAsset.name}…`);
  const swapArchive = path.join(tmp, swapAsset.name);
  await download(swapAsset.browser_download_url, swapArchive);
  const swapDir = path.join(tmp, "swap");
  extract(swapArchive, swapDir);
  const swapBin = findFile(swapDir, exe("llama-swap"));
  if (!swapBin) throw new Error("llama-swap not found in archive");
  fs.copyFileSync(swapBin, path.join(BIN, exe("llama-swap")));

  if (!isWin) {
    fs.chmodSync(path.join(BIN, "llama-server"), 0o755);
    fs.chmodSync(path.join(BIN, "llama-swap"), 0o755);
  }
  console.log(`Binaries → ${path.relative(ROOT, BIN)}`);
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}

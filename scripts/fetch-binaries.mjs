#!/usr/bin/env node
// Cross-platform: download the right llama.cpp (llama-server + libs) and
// llama-swap binaries for this OS/arch into vendor/bin. Idempotent.
// Extraction uses `tar`, which handles .tar.gz and .zip on macOS, Linux, and
// Windows 10+ (bsdtar). Run via `npm run binaries`.
//
// Releases are PINNED and every downloaded archive is verified against its
// expected SHA-256 before extraction — a compromised, re-tagged, or renamed
// upstream release fails loudly instead of shipping to users.
//
// To bump a pin: pick a release where every asset below exists, then refresh
// the digests straight from the GitHub API (it reports per-asset sha256):
//   gh api repos/ggml-org/llama.cpp/releases/tags/<tag> \
//     --jq '.assets[] | "\(.name) \(.digest)"'
//   gh api repos/mostlygeek/llama-swap/releases/tags/<tag> \
//     --jq '.assets[] | "\(.name) \(.digest)"'
// (Not every llama.cpp CI build publishes all six platform assets — check.)

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const LCPP_TAG = "b10019";
const LCPP_SHA256 = {
  "llama-b10019-bin-macos-arm64.tar.gz": "059ba8f859edeb321d45d51caca3adb67571fd5c1a6c1d7d9b42254b68dd943b",
  "llama-b10019-bin-macos-x64.tar.gz": "4ccfdfad0933fdc341a6cdccb17ca201a9e8843080c2fd379b7de717283702ff",
  "llama-b10019-bin-ubuntu-arm64.tar.gz": "3969ece56aa0a5c8daa550b15ca3a9eabc063a2a9c0516a50fe307dc2f3101f6",
  "llama-b10019-bin-ubuntu-x64.tar.gz": "dca9238166b038ca9144d20952247edacaadc531cd4555e07242185342da3e30",
  "llama-b10019-bin-win-cpu-arm64.zip": "806690f8961255694319aaf5c9e51552078d02ea8019e29338adba83b4f6c87b",
  "llama-b10019-bin-win-cpu-x64.zip": "38c073328c63c6103bb8f28f4d426d386852e92af6cff6310fe70759fedbca1f",
};

const SWAP_TAG = "v240";
const SWAP_SHA256 = {
  "llama-swap_240_darwin_amd64.tar.gz": "b06388e20a9ec0d7e6762d2f0b00071d814650396e4dac35b78392731d6e2dd2",
  "llama-swap_240_darwin_arm64.tar.gz": "389f7e493030e4fcbd725fe07e23a1c06b36d858740c910f94c7e239ecb96323",
  "llama-swap_240_linux_amd64.tar.gz": "3e0c3fd2649f2b0eb417ab2bc337da65e3bbb5374fae9769e74ab90bdaa3739c",
  "llama-swap_240_linux_arm64.tar.gz": "1a7cf96361ae849ab2cbb808062144be490d7c65454d7896fd318e575c5172ed",
  "llama-swap_240_windows_amd64.zip": "ebdc3465809923acacf0064fcab7dbd1b745cfae239be72590c8cd94b172177d",
  // no upstream windows_arm64 build — that combo errors out below, as before
};

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
  "darwin-arm64": `llama-${LCPP_TAG}-bin-macos-arm64.tar.gz`,
  "darwin-x64": `llama-${LCPP_TAG}-bin-macos-x64.tar.gz`,
  "linux-x64": `llama-${LCPP_TAG}-bin-ubuntu-x64.tar.gz`,
  "linux-arm64": `llama-${LCPP_TAG}-bin-ubuntu-arm64.tar.gz`,
  "win32-x64": `llama-${LCPP_TAG}-bin-win-cpu-x64.zip`,
  "win32-arm64": `llama-${LCPP_TAG}-bin-win-cpu-arm64.zip`,
}[`${process.platform}-${arch}`];

if (!LCPP_ASSET) {
  console.error(`Unsupported platform/arch: ${process.platform}-${arch}`);
  process.exit(1);
}

async function download(url, dest, expectedSha256) {
  const r = await fetch(url);
  if (!r.ok || !r.body) throw new Error(`download ${url} -> ${r.status}`);
  const out = fs.createWriteStream(dest);
  const hash = createHash("sha256");
  for await (const chunk of r.body) {
    const buf = Buffer.from(chunk);
    hash.update(buf);
    if (!out.write(buf)) await new Promise((res) => out.once("drain", res));
  }
  await new Promise((res, rej) => out.end((e) => (e ? rej(e) : res())));
  const actual = hash.digest("hex");
  if (actual !== expectedSha256) {
    fs.rmSync(dest, { force: true });
    throw new Error(
      `SHA-256 mismatch for ${path.basename(dest)}\n  expected ${expectedSha256}\n  got      ${actual}\n` +
        "The upstream release differs from the pinned digest — refusing to install it."
    );
  }
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
  console.log(`Fetching ${LCPP_ASSET} (${LCPP_TAG}, pinned)…`);
  const lcppArchive = path.join(tmp, LCPP_ASSET);
  await download(
    `https://github.com/ggml-org/llama.cpp/releases/download/${LCPP_TAG}/${LCPP_ASSET}`,
    lcppArchive,
    LCPP_SHA256[LCPP_ASSET]
  );
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
  const swapAsset = `llama-swap_${SWAP_TAG.replace(/^v/, "")}_${swapOs}_${swapArch}.${isWin ? "zip" : "tar.gz"}`;
  if (!SWAP_SHA256[swapAsset]) throw new Error(`no pinned llama-swap asset for ${swapOs}/${swapArch}`);
  console.log(`Fetching ${swapAsset} (${SWAP_TAG}, pinned)…`);
  const swapArchive = path.join(tmp, swapAsset);
  await download(
    `https://github.com/mostlygeek/llama-swap/releases/download/${SWAP_TAG}/${swapAsset}`,
    swapArchive,
    SWAP_SHA256[swapAsset]
  );
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

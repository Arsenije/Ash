# Ash

A **local, offline-first photo gallery** that turns a folder of photos into a searchable, connected knowledge base. Drag photos in; an LLM describes each; [khora](https://github.com/DeytaHQ/khora) stores the descriptions, extracted entities, and structured attributes and links photos by shared places, objects, and scenes. Then browse, filter, and explore — fully locally.

Photos are **never copied or uploaded for storage** — they stay in their original location; only the description and a file path are stored.

## How it works

```
Electron (UI)  ⇄  Python sidecar (FastAPI)  ⇄  khora (embedded)  →  llama-swap → llama.cpp (local GGUF models)
```

- **Electron app** (`electron/`, `renderer/`) — drag-and-drop, a faceted search bar (type-ahead filter chips + semantic search), a category-grouped Themes view, and a detail view with related photos. It also manages the local runtime: downloads the model GGUFs and runs `llama-swap`.
- **Python sidecar** (`sidecar/`) — FastAPI service that calls a local vision model (through the OpenAI-compatible endpoint) to describe each photo and stores everything through khora.
- **[llama.cpp](https://github.com/ggml-org/llama.cpp) + [llama-swap](https://github.com/mostlygeek/llama-swap)** — `llama-server` runs the GGUF models; `llama-swap` fronts them with **one** OpenAI-compatible endpoint and swaps models on demand (vision, embeddings, text). The sidecar and khora only ever see that one endpoint.
- **khora**, embedded — `sqlite_lance` backend + `vectorcypher` engine: SQLite + LanceDB in-process, **no Docker, no external services**. Entity extraction + resolution gives the cross-photo "connections" without Neo4j.

**Everything runs on your machine** — vision description, embeddings, entity extraction, and search all go through local GGUF models. No account, no API keys, no network.

## Setup

**Apple Silicon strongly recommended** — see [Hardware](#hardware). One command does everything:

```bash
npm run setup
```

It checks for the base tools (**Homebrew**, **Node**, **uv**) and **offers to install any that are missing**, then installs the model runtime (`llama.cpp` + `llama-swap`), the Python 3.13 sidecar (`khora` + FastAPI), and the Electron dependencies, and launches the app. It's idempotent — re-running just starts Ash. After the first time, `npm start` is enough.

> If you don't have Node yet, the very first run needs `bash scripts/setup.sh` (since `npm` isn't available until Node is installed); it'll take it from there.

<details>
<summary>What the one command does (or run the steps by hand)</summary>

```bash
brew install llama.cpp llama-swap                       # model runtime
cd sidecar && uv venv --python 3.13 .venv \             # python sidecar
  && uv pip install --python .venv/bin/python "khora[embedded]" fastapi "uvicorn[standard]" openai pillow python-multipart && cd ..
npm install                                             # electron deps
npm start                                               # run
```
</details>

The app finds the runtime binaries in its bundle (`Resources/bin/`) first, then falls back to `vendor/bin/` and Homebrew for development.

On first launch Ash walks you through a short setup:

1. **Yes / No** — what Ash does and doesn't do: photos stay on your machine, no account, no tracking, no network.
2. **Set up the AI model** — Ash downloads three GGUF models from HuggingFace (~3.5 GB, once): SmolVLM2 2.2B (vision, + its mmproj projector), Nomic Embed v1.5 (embeddings), and Qwen2.5 3B (entity extraction). On Intel or low-memory machines you'll get a heads-up first that it'll be slow.

Then drag in some photos. The OpenAI SDK is used only as the HTTP client for the local `llama-swap` endpoint — no key, no account.

## Hardware

- **Apple Silicon (M1–M4):** runs on the GPU via Metal, zero config. A base 8 GB M1 handles the bundled models fine; 16 GB is comfortable.
- **Intel Macs:** CPU-only here — describing a photo can take a minute or more. Usable for small libraries, slow for bulk imports.
- **Memory:** 8 GB is the practical floor; under that, ingest may stall.

## Features

- **Drag-and-drop ingest** with per-photo scanning status (optimistic thumbnails + spinners) and a live progress toast.
- **Faceted search bar** — type a field (`location`, `scene`, `object`, `tag`, `after`, `before`) to get a type-to-filter value dropdown that becomes a removable chip; arrow-key + Enter navigation; leftover words run a semantic search alongside the chips.
- **Themes** — photos auto-grouped by category (Animals / Places / Objects / Scenes) and by recurring entities, from khora's resolved entity graph.
- **Detail + related** — full description, attributes, and visually/semantically related photos.

## Status

Working vertical slice, local-only. Built on khora's embedded stack; see the code for the storage and retrieval wiring. Not yet packaged as a distributable binary (runs from source via `npm start` + a local Python venv, with the llama.cpp/llama-swap binaries installed separately). Bundling + signing those binaries into the `.app` is the remaining packaging step.

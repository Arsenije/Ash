# Ash

A **local, offline-first photo gallery** that turns a folder of photos into a searchable, connected knowledge base. Drag photos in; an LLM describes each; [khora](https://github.com/DeytaHQ/khora) stores the descriptions, extracted entities, and structured attributes and links photos by shared places, objects, and scenes. Then browse, filter, and explore — fully locally.

Photos are **never copied or uploaded for storage** — they stay in their original location; only the description and a file path are stored.

## How it works

```
Electron (UI)  ⇄  Python sidecar (FastAPI)  ⇄  khora (embedded)  →  OpenAI (describe/embed)
```

- **Electron app** (`electron/`, `renderer/`) — drag-and-drop, a faceted search bar (type-ahead filter chips + semantic search), a category-grouped Themes view, a detail view with related photos, and a live cost label.
- **Python sidecar** (`sidecar/`) — FastAPI service that calls OpenAI's vision model to describe each photo and stores everything through khora.
- **khora**, embedded — `sqlite_lance` backend + `vectorcypher` engine: SQLite + LanceDB in-process, **no Docker, no external services**. Entity extraction + resolution gives the cross-photo "connections" without Neo4j.

Everything runs locally except, during ingest, the OpenAI calls (vision description, text embeddings, entity extraction) and the query embedding for semantic search.

## Setup

Requires Python 3.13, Node 18+, and an OpenAI API key.

```bash
# 1. Sidecar deps (installs khora with the embedded extra + FastAPI/OpenAI/Pillow)
cd sidecar
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python "khora[embedded]" fastapi "uvicorn[standard]" openai pillow python-multipart
cd ..

# 2. Electron deps
npm install

# 3. Run
npm start
```

Then open **Settings (⚙)**, paste your OpenAI API key (stored locally, encrypted via Electron `safeStorage`), and drag in some photos.

## Features

- **Drag-and-drop ingest** with per-photo scanning status (optimistic thumbnails + spinners) and a live progress toast.
- **Faceted search bar** — type a field (`location`, `scene`, `object`, `tag`, `after`, `before`) to get a type-to-filter value dropdown that becomes a removable chip; arrow-key + Enter navigation; leftover words run a semantic search alongside the chips.
- **Themes** — photos auto-grouped by category (Animals / Places / Objects / Scenes) and by recurring entities, from khora's resolved entity graph.
- **Detail + related** — full description, attributes, and visually/semantically related photos.
- **Measured cost label** — real OpenAI token usage tallied per library, not an estimate.

## Status

Working vertical slice. Built on khora's embedded stack; see the code for the storage and retrieval wiring. Not yet packaged as a distributable binary (runs from source via `npm start` + a local Python venv).

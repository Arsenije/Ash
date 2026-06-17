"""Embedded khora lifecycle for the photo-gallery sidecar.

Binds a single process-wide ``Khora`` instance to the embedded stack
(SQLite + LanceDB via ``sqlite_lance``) running the ``vectorcypher`` engine —
no Docker, no external services. All data lives under ``KHORA_PHOTO_DATA_DIR``
(set by the Electron app to its userData dir; defaults to ``./data`` for
standalone runs).
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from khora import Khora
from khora.config.schema import KhoraConfig, SQLiteLanceConfig

DATA_DIR = Path(os.environ.get("KHORA_PHOTO_DATA_DIR", "./data")).expanduser().resolve()
THUMBS_DIR = DATA_DIR / "thumbnails"

# The embedding model (and so the vector dimension) is set by the chosen engine.
# Defaults match OpenAI's text-embedding-3-small for standalone runs.
EMBED_DIM = int(os.environ.get("KHORA_EMBED_DIM", "1536"))

# One store per embedding dimension: vectors from a 1536-dim model and a 768-dim
# model can't share a table, so switching engines opens a separate library
# instead of crashing on a dimension mismatch.
_NS_FILE = DATA_DIR / f"namespace-{EMBED_DIM}.txt"

_kb: Khora | None = None
_namespace: UUID | None = None


def _build_config() -> KhoraConfig:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    config = KhoraConfig()
    config.storage.backend = "sqlite_lance"
    config.storage.sqlite_lance = SQLiteLanceConfig(
        db_path=str(DATA_DIR / f"khora-{EMBED_DIM}.db"),
        lance_path=str(DATA_DIR / f"khora-{EMBED_DIM}.lance"),
        embedding_dimension=EMBED_DIM,
    )
    config.llm.embedding_dimension = EMBED_DIM
    # Models come from the engine env (set by the Electron app):
    #   KHORA_LLM_MODEL / KHORA_EXTRACTION_MODEL / KHORA_EMBED_MODEL.
    # For local/custom engines these carry an ``openai/`` prefix and litellm is
    # pointed at the OpenAI-compatible server via OPENAI_BASE_URL / OPENAI_API_BASE.
    # Unset -> khora's defaults (OpenAI hosted). Reranking stays OFF (no torch).
    if model := os.environ.get("KHORA_LLM_MODEL"):
        config.llm.model = model
    if ext := os.environ.get("KHORA_EXTRACTION_MODEL"):
        config.llm.extraction_model = ext
    if emb := os.environ.get("KHORA_EMBED_MODEL"):
        config.llm.embedding_model = emb
    return config


async def startup() -> None:
    global _kb, _namespace
    config = _build_config()
    _kb = Khora(config, engine="vectorcypher", run_migrations=True)
    await _kb.connect()
    _namespace = await _ensure_namespace(_kb)


async def shutdown() -> None:
    global _kb, _namespace
    if _kb is not None:
        await _kb.disconnect()
    _kb = None
    _namespace = None


def kb() -> Khora:
    if _kb is None:
        raise RuntimeError("khora is not connected")
    return _kb


def namespace() -> UUID:
    if _namespace is None:
        raise RuntimeError("namespace is not initialized")
    return _namespace


async def _ensure_namespace(instance: Khora) -> UUID:
    """One stable namespace per data dir, persisted next to the DB."""
    if _NS_FILE.exists():
        return UUID(_NS_FILE.read_text().strip())
    ns = await instance.create_namespace()
    nid: UUID = ns.namespace_id
    _NS_FILE.write_text(str(nid))
    return nid

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
_NS_FILE = DATA_DIR / "namespace.txt"
EMBED_DIM = 1536  # text-embedding-3-small; the embedded store expects this.

_kb: Khora | None = None
_namespace: UUID | None = None


def _build_config() -> KhoraConfig:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    config = KhoraConfig()
    config.storage.backend = "sqlite_lance"
    config.storage.sqlite_lance = SQLiteLanceConfig(
        db_path=str(DATA_DIR / "khora.db"),
        lance_path=str(DATA_DIR / "khora.lance"),
        embedding_dimension=EMBED_DIM,
    )
    config.llm.embedding_dimension = EMBED_DIM
    # Embeddings + extraction go through OpenAI (litellm) using OPENAI_API_KEY.
    # Cross-encoder reranking is left OFF so torch is never imported at runtime.
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

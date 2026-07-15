"""Test harness for the sidecar.

Everything at module top level here runs BEFORE any application import, which
matters twice over:

1. ``KHORA_PHOTO_DATA_DIR`` must point at a throwaway dir before importing
   ``khora_client``/``server`` — both compute paths from it at import time
   (``DATA_DIR``, ``THUMBS_DIR``, ``server._USAGE_FILE``).

2. A lightweight fake ``khora`` package is injected into ``sys.modules`` so the
   suite runs without installing ``khora[embedded]`` (which drags in torch).
   The fake is registered unconditionally — even if a real khora is importable
   — so test behavior never depends on the ambient environment. Only the names
   the sidecar imports at module level are provided; all *behavior* goes
   through the ``FakeKB`` fixture, wired directly into ``khora_client``'s
   module globals (``_kb``/``_namespace``), which is exactly how the real
   ``startup()`` publishes the live instance.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from pathlib import Path

import pytest

# --- 1. environment, before any app import --------------------------------
_DATA_DIR = tempfile.mkdtemp(prefix="ash-test-data-")
os.environ["KHORA_PHOTO_DATA_DIR"] = _DATA_DIR
os.environ.pop("PHOTO_SIDECAR_TOKEN", None)  # auth off by default; tests set server.SIDECAR_TOKEN


# --- 2. fake khora package -------------------------------------------------
def _register(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


class _KwargsObject:
    """Stand-in for khora config/expertise classes: accepts anything."""

    def __init__(self, *args, **kwargs):
        self.__dict__.update(kwargs)


class _FakeKhora(_KwargsObject):
    pass


_khora = _register("khora")
_khora.Khora = _FakeKhora
_khora_config = _register("khora.config")
_khora_schema = _register("khora.config.schema")
_khora_schema.KhoraConfig = type("KhoraConfig", (_KwargsObject,), {})
_khora_schema.SQLiteLanceConfig = type("SQLiteLanceConfig", (_KwargsObject,), {})
_khora_config.schema = _khora_schema
_khora.config = _khora_config
_khora_extraction = _register("khora.extraction")
_khora_skills = _register("khora.extraction.skills")
_khora_skills_base = _register("khora.extraction.skills.base")
_khora_skills_base.EntityTypeConfig = type("EntityTypeConfig", (_KwargsObject,), {})
_khora_skills_base.RelationshipTypeConfig = type("RelationshipTypeConfig", (_KwargsObject,), {})
_khora_skills_base.ExpertiseConfig = type("ExpertiseConfig", (_KwargsObject,), {})
_khora_skills.base = _khora_skills_base
_khora_extraction.skills = _khora_skills
_khora.extraction = _khora_extraction

# --- 3. make the sidecar importable and its dirs real ----------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import khora_client as kc  # noqa: E402  (needs the env + fakes above)

kc.THUMBS_DIR.mkdir(parents=True, exist_ok=True)


# --- 4. FakeKB: in-memory stand-in for khora's document store --------------
class FakeDoc:
    def __init__(
        self,
        *,
        external_id: str | None = None,
        content: str = "",
        title: str | None = None,
        source_url: str | None = None,
        source_timestamp=None,
        metadata: dict | None = None,
    ):
        self.id = uuid.uuid4()
        self.external_id = external_id
        self.content = content
        self.title = title
        self.source_url = source_url
        self.source_timestamp = source_timestamp
        self.metadata = metadata or {}


class FakeResult:
    def __init__(self, document_id):
        self.document_id = document_id
        self.llm_usage: list = []


class FakeKB:
    """Mirrors the khora semantics the sidecar relies on.

    ``remember()`` with an ``external_id`` that already exists replaces that
    document in place and returns the SAME document id (verified against
    khora's vectorcypher engine, ``_remember_via_replace``); otherwise it
    creates a new document. ``calls`` records the operation order so tests can
    assert e.g. remember-before-forget.
    """

    def __init__(self):
        self.docs: dict[uuid.UUID, FakeDoc] = {}
        self.calls: list[tuple[str, object]] = []
        self.remember_error: Exception | None = None
        self.remember_delay: float = 0.0
        self.forget_error: Exception | None = None

    def seed(self, **kwargs) -> FakeDoc:
        doc = FakeDoc(**kwargs)
        self.docs[doc.id] = doc
        return doc

    async def list_documents(self, *, namespace, limit=100):
        return list(self.docs.values())

    async def get_document(self, doc_id, *, namespace):
        return self.docs.get(doc_id)

    async def remember(
        self,
        *,
        content,
        namespace,
        external_id=None,
        metadata=None,
        title=None,
        source_url=None,
        source_timestamp=None,
        **_ignored,
    ):
        self.calls.append(("remember", external_id))
        if self.remember_delay:
            import asyncio

            await asyncio.sleep(self.remember_delay)
        if self.remember_error is not None:
            raise self.remember_error
        existing = None
        if external_id is not None:
            existing = next((d for d in self.docs.values() if d.external_id == external_id), None)
        target = existing or FakeDoc(external_id=external_id)
        target.content = content
        target.title = title
        target.source_url = source_url
        target.source_timestamp = source_timestamp
        target.metadata = metadata or {}
        self.docs[target.id] = target
        return FakeResult(target.id)

    async def forget(self, doc_id, *, namespace):
        self.calls.append(("forget", doc_id))
        if self.forget_error is not None:
            raise self.forget_error
        return self.docs.pop(doc_id, None) is not None


@pytest.fixture
def fake_kb():
    kb = FakeKB()
    kc._kb = kb
    kc._namespace = uuid.uuid4()
    yield kb
    kc._kb = None
    kc._namespace = None

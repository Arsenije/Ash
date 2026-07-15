"""Regression tests for rescan's data-safety ordering.

Rescan must never delete a document unless its replacement already exists:
``remember()`` (an in-place replace when the external_id matches) comes first,
and ``forget()`` runs only for a superseded row under a different id.
A failing or hung model must leave the library exactly as it was.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from PIL import Image

import server


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "photo.png"
    Image.new("RGB", (8, 8), "orange").save(p)
    return p


@pytest.fixture
def fake_describe(monkeypatch):
    async def _describe(path):
        desc = {
            "description": "a small orange square",
            "location": "test bench",
            "objects": ["square"],
            "animals": [],
            "scene": "testing",
            "tags": ["orange"],
        }
        return desc, {"model": "fake-vlm", "input": 1, "output": 1}

    monkeypatch.setattr(server, "describe_image", _describe)


def _items(kb):
    """Build rescan items the same way the /rescan endpoint does."""
    return [
        {
            "doc_id": d.id,
            "external_id": d.external_id,
            "title": d.title,
            "source_url": d.source_url,
            "source_timestamp": d.source_timestamp,
            "content": d.content or "",
            "metadata": d.metadata or {},
        }
        for d in kb.docs.values()
    ]


async def _run(kb, mode):
    job_id = uuid.uuid4().hex
    server._register_job(
        job_id,
        {"total": len(kb.docs), "done": 0, "skipped": 0, "failed": 0, "status": "running", "errors": []},
    )
    await server._run_rescan(job_id, _items(kb), mode)
    return server._jobs[job_id]


# --- describe mode ----------------------------------------------------------

async def test_describe_replaces_in_place_without_forget(fake_kb, photo, fake_describe):
    doc = fake_kb.seed(
        external_id="ext-1",
        content="old description",
        source_url=str(photo),
        metadata={"custom": {"location": "old", "immich_asset_id": "im-42"}},
    )
    job = await _run(fake_kb, "describe")

    assert job["done"] == 1 and job["failed"] == 0
    assert [op for op, _ in fake_kb.calls] == ["remember"]  # no forget, ever
    assert doc.id in fake_kb.docs  # same document id survives
    replaced = fake_kb.docs[doc.id]
    assert "orange square" in replaced.content
    custom = replaced.metadata["custom"]
    assert custom["immich_asset_id"] == "im-42"  # keys rescan doesn't recompute survive
    assert custom["location"] == "test bench"  # recomputed keys win


async def test_describe_remember_failure_keeps_doc(fake_kb, photo, fake_describe):
    doc = fake_kb.seed(external_id="ext-1", content="old description", source_url=str(photo))
    fake_kb.remember_error = RuntimeError("model exploded")

    job = await _run(fake_kb, "describe")

    assert job["failed"] == 1
    assert doc.id in fake_kb.docs and fake_kb.docs[doc.id].content == "old description"
    assert ("forget", doc.id) not in fake_kb.calls


async def test_describe_remember_timeout_keeps_doc(fake_kb, photo, fake_describe, monkeypatch):
    doc = fake_kb.seed(external_id="ext-1", content="old description", source_url=str(photo))
    fake_kb.remember_delay = 5.0
    monkeypatch.setattr(server, "REMEMBER_TIMEOUT_S", 0.05)

    job = await _run(fake_kb, "describe")

    assert job["failed"] == 1
    assert job["errors"][0]["error"] == "timed out"
    assert doc.id in fake_kb.docs and fake_kb.docs[doc.id].content == "old description"
    assert ("forget", doc.id) not in fake_kb.calls


async def test_describe_superseded_doc_forgotten_after_remember(fake_kb, photo, fake_describe):
    # A doc without an external_id can't be replaced in place: remember()
    # creates a fresh doc (keyed by the file hash) and only then may the old
    # row be dropped.
    doc = fake_kb.seed(external_id=None, content="old description", source_url=str(photo))

    job = await _run(fake_kb, "describe")

    assert job["done"] == 1
    ops = [op for op, _ in fake_kb.calls]
    assert ops == ["remember", "forget"]  # forget strictly after the replacement exists
    assert doc.id not in fake_kb.docs
    assert len(fake_kb.docs) == 1  # exactly the replacement remains


async def test_describe_forget_failure_is_nonfatal(fake_kb, photo, fake_describe):
    doc = fake_kb.seed(external_id=None, content="old description", source_url=str(photo))
    fake_kb.forget_error = RuntimeError("store hiccup")

    job = await _run(fake_kb, "describe")

    assert job["done"] == 1 and job["failed"] == 0  # photo counted as rescanned
    assert doc.id in fake_kb.docs  # transient duplicate kept, nothing lost
    assert any("superseded" in e["error"] for e in job["errors"])


async def test_describe_missing_file_skips(fake_kb, fake_describe):
    fake_kb.seed(external_id="ext-1", content="old", source_url="/nowhere/gone.jpg")
    job = await _run(fake_kb, "describe")
    assert job["skipped"] == 1
    assert fake_kb.calls == []  # untouched


# --- extract mode -----------------------------------------------------------

async def test_extract_replaces_in_place_without_forget(fake_kb):
    doc = fake_kb.seed(
        external_id="ext-1",
        content="a cat on a sofa",
        metadata={"custom": {"immich_asset_id": "im-42"}},
    )
    job = await _run(fake_kb, "extract")

    assert job["done"] == 1
    assert [op for op, _ in fake_kb.calls] == ["remember"]
    assert doc.id in fake_kb.docs
    assert fake_kb.docs[doc.id].content == "a cat on a sofa"
    assert fake_kb.docs[doc.id].metadata["custom"]["immich_asset_id"] == "im-42"


async def test_extract_remember_failure_keeps_doc(fake_kb):
    doc = fake_kb.seed(external_id="ext-1", content="a cat on a sofa")
    fake_kb.remember_error = RuntimeError("extraction model down")

    job = await _run(fake_kb, "extract")

    assert job["failed"] == 1
    assert doc.id in fake_kb.docs and fake_kb.docs[doc.id].content == "a cat on a sofa"
    assert ("forget", doc.id) not in fake_kb.calls


async def test_extract_unhealthy_model_trips_breaker_without_losing_docs(fake_kb, monkeypatch):
    monkeypatch.setattr(server, "CIRCUIT_FAILS", 2)
    docs = [fake_kb.seed(external_id=f"ext-{i}", content=f"photo {i}") for i in range(6)]
    fake_kb.remember_error = asyncio.TimeoutError()

    job = await _run(fake_kb, "extract")

    assert job["aborted"] is True
    assert job["failed"] >= 2
    assert job["failed"] + job["skipped"] == 6
    assert all(d.id in fake_kb.docs for d in docs)  # the library survived intact

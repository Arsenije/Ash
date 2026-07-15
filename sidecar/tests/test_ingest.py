"""Ingest job lifecycle: dedup, per-item status, failure isolation, breaker."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from PIL import Image

import server


def _image(tmp_path, name, color):
    p = tmp_path / name
    Image.new("RGB", (8, 8), color).save(p)
    return p


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def fake_describe(monkeypatch):
    """describe_image stub; set .fail_names to make specific files fail."""

    class _Describe:
        fail_names: set[str] = set()

        async def __call__(self, path):
            if path.name in self.fail_names:
                raise RuntimeError("vision model unavailable")
            desc = {
                "description": f"test photo {path.name}",
                "location": "bench",
                "objects": [],
                "animals": [],
                "scene": "",
                "tags": ["test"],
            }
            return desc, {"model": "fake-vlm", "input": 1, "output": 1}

    stub = _Describe()
    monkeypatch.setattr(server, "describe_image", stub)
    return stub


async def _run(files):
    job_id = uuid.uuid4().hex
    server._register_job(job_id, {
        "total": len(files),
        "done": 0,
        "skipped": 0,
        "failed": 0,
        "status": "running",
        "errors": [],
        "items": {str(f): {"status": "pending"} for f in files},
    })
    await server._run_ingest(job_id, files)
    return server._jobs[job_id]


async def test_ingest_creates_documents_keyed_by_content_hash(fake_kb, fake_describe, tmp_path):
    a = _image(tmp_path, "a.png", "red")
    b = _image(tmp_path, "b.png", "blue")

    job = await _run([a, b])

    assert job["status"] == "done"
    assert job["done"] == 2 and job["failed"] == 0 and job["skipped"] == 0
    assert {d.external_id for d in fake_kb.docs.values()} == {_sha256(a), _sha256(b)}
    assert job["items"][str(a)]["status"] == "done"
    assert job["items"][str(a)]["doc_id"]  # renderer uses this to wire the card click


async def test_ingest_skips_already_ingested_bytes(fake_kb, fake_describe, tmp_path):
    a = _image(tmp_path, "a.png", "red")
    fake_kb.seed(external_id=_sha256(a), content="already here")

    job = await _run([a])

    assert job["skipped"] == 1 and job["done"] == 0
    assert len(fake_kb.docs) == 1  # nothing new was written


async def test_ingest_failure_is_isolated_per_file(fake_kb, fake_describe, tmp_path):
    good = _image(tmp_path, "good.png", "green")
    bad = _image(tmp_path, "bad.png", "black")
    fake_describe.fail_names = {"bad.png"}

    job = await _run([bad, good])

    assert job["done"] == 1 and job["failed"] == 1
    assert job["items"][str(good)]["status"] == "done"
    assert job["items"][str(bad)]["status"] == "failed"
    assert "vision model unavailable" in job["items"][str(bad)]["error"]
    assert job["errors"] and job["errors"][0]["path"] == str(bad)


async def test_ingest_breaker_stops_hammering_a_dead_model(fake_kb, fake_describe, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CIRCUIT_FAILS", 2)
    files = [_image(tmp_path, f"p{i}.png", (10 * i, 0, 0)) for i in range(6)]
    fake_describe.fail_names = {f.name for f in files}

    job = await _run(files)

    assert job["aborted"] is True
    assert job["failed"] >= 2  # at least the trip threshold actually failed
    assert job["skipped"] >= 1  # the tail was skipped, not attempted
    assert job["failed"] + job["skipped"] == 6
    assert any("Stopped after" in e.get("error", "") for e in job["errors"])


def test_expand_paths_recurses_filters_and_dedupes(tmp_path):
    nested = tmp_path / "trip" / "day1"
    nested.mkdir(parents=True)
    img = _image(nested, "photo.JPG", "red")  # extension check is case-insensitive
    _ = (nested / "notes.txt").write_text("not a photo")
    single = _image(tmp_path, "single.png", "blue")

    out = server._expand_paths([str(tmp_path), str(single), str(img)])  # overlapping inputs

    assert sorted(p.name for p in out) == ["photo.JPG", "single.png"]
    assert len(out) == len({str(p) for p in out})  # resolved + deduped

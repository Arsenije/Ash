"""/search graph fusion and /related — the endpoints with batched doc fetches."""

from __future__ import annotations

import httpx
import pytest

import server


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as c:
        yield c


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(server, "SIDECAR_TOKEN", "sekrit")
    return {"X-Ash-Token": "sekrit"}


async def test_related_ranks_by_shared_entity_count(client, token, fake_kb):
    target = fake_kb.seed(external_id="t", content="target photo")
    a = fake_kb.seed(external_id="a", content="photo a")
    b = fake_kb.seed(external_id="b", content="photo b")
    fake_kb.seed_entity("castle", [target.id, a.id, b.id])
    fake_kb.seed_entity("beach", [target.id, a.id])

    res = await client.get(f"/related/{target.id}", headers=token)

    assert res.status_code == 200
    photos = res.json()["photos"]
    assert [p["id"] for p in photos] == [str(a.id), str(b.id)]  # most shared first
    assert photos[0]["shared_entities"] == 2
    assert photos[1]["shared_entities"] == 1


async def test_related_no_shared_entities(client, token, fake_kb):
    target = fake_kb.seed(external_id="t", content="loner")
    res = await client.get(f"/related/{target.id}", headers=token)
    assert res.json()["photos"] == []


async def test_search_fuses_graph_only_hits(client, token, fake_kb):
    d1 = fake_kb.seed(external_id="d1", content="a cat on a sofa")
    d2 = fake_kb.seed(external_id="d2", content="the same cat outdoors")
    fake_kb.set_recall([d1], [(d1.id, "a cat on a sofa")])
    fake_kb.seed_entity("cat", [d2.id])  # d2 arrives via the entity graph only

    res = await client.get("/search?q=cat", headers=token)

    data = res.json()
    ids = [p["id"] for p in data["photos"]]
    assert str(d1.id) in ids and str(d2.id) in ids
    assert data["mode"] == "semantic+graph"
    # Recall hits carry their best chunk as the description; graph-only hits
    # fall back to the stored content.
    by_id = {p["id"]: p for p in data["photos"]}
    assert by_id[str(d1.id)]["description"] == "a cat on a sofa"
    assert by_id[str(d2.id)]["description"] == "the same cat outdoors"


async def test_search_graph_hits_respect_chip_filters(client, token, fake_kb):
    d1 = fake_kb.seed(
        external_id="d1", content="cat in the kitchen", metadata={"custom": {"location": "kitchen"}}
    )
    d2 = fake_kb.seed(
        external_id="d2", content="cat at the beach", metadata={"custom": {"location": "beach"}}
    )
    fake_kb.set_recall([d1], [(d1.id, "cat in the kitchen")])
    fake_kb.seed_entity("cat", [d2.id])

    res = await client.get("/search?q=cat&location=kitchen", headers=token)

    ids = [p["id"] for p in res.json()["photos"]]
    assert str(d1.id) in ids  # recall hits were already filtered store-side
    assert str(d2.id) not in ids  # graph injection must honour the chip filter


async def test_search_without_graph_hits_is_semantic(client, token, fake_kb):
    d1 = fake_kb.seed(external_id="d1", content="a dog")
    fake_kb.set_recall([d1], [(d1.id, "a dog")])

    res = await client.get("/search?q=dog", headers=token)

    data = res.json()
    assert data["mode"] == "semantic"
    assert [p["id"] for p in data["photos"]] == [str(d1.id)]

"""Token middleware + endpoint hardening, over real HTTP (ASGI transport).

The lifespan deliberately does not run (no kc.startup) — the FakeKB fixture
provides the store, which is exactly how the middleware behaves in production
when a request races startup.
"""

from __future__ import annotations

import httpx
import pytest

import khora_client as kc
import server


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as c:
        yield c


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(server, "SIDECAR_TOKEN", "sekrit")
    return "sekrit"


async def test_no_token_is_401(client, token, fake_kb):
    res = await client.get("/health")
    assert res.status_code == 401


async def test_wrong_token_is_401(client, token, fake_kb):
    res = await client.get("/health", headers={"X-Ash-Token": "wrong"})
    assert res.status_code == 401


async def test_header_token_passes(client, token, fake_kb):
    res = await client.get("/health", headers={"X-Ash-Token": token})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_query_param_token_passes(client, token, fake_kb):
    # <img> elements can't set headers; the renderer appends ?token= instead.
    res = await client.get(f"/health?token={token}")
    assert res.status_code == 200


async def test_options_preflight_bypasses_token(client, token):
    # CORS preflight carries no custom headers; it must reach CORSMiddleware.
    res = await client.options(
        "/health",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-ash-token",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "*"


async def test_empty_token_env_disables_gate(client, fake_kb, monkeypatch):
    monkeypatch.setattr(server, "SIDECAR_TOKEN", "")
    res = await client.get("/health")
    assert res.status_code == 200


async def test_thumb_only_serves_hex_named_files(client, fake_kb, tmp_path):
    (kc.THUMBS_DIR / "abc123.webp").write_bytes(b"not-really-webp")
    ok = await client.get("/thumb/abc123")
    assert ok.status_code == 200

    # Anything but [0-9a-f] is stripped before the path is built, so traversal
    # can't escape THUMBS_DIR.
    evil = await client.get("/thumb/..%2F..%2Fetc%2Fpasswd")
    assert evil.status_code == 404


async def test_photo_malformed_uuid_is_404_not_500(client, fake_kb):
    res = await client.get("/photo/not-a-uuid")
    assert res.status_code == 404

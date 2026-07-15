"""Immich client: URL normalization, pagination cursor, atomic downloads."""

from __future__ import annotations

import json

import httpx
import pytest

import immich_client


# --- normalize_base_url ------------------------------------------------------

def test_normalize_strips_trailing_slash_and_api():
    assert immich_client.normalize_base_url("https://x.example/") == "https://x.example"
    assert immich_client.normalize_base_url("https://x.example/api") == "https://x.example"
    assert immich_client.normalize_base_url("http://10.0.0.5:2283") == "http://10.0.0.5:2283"


@pytest.mark.parametrize("bad", ["", "   ", "ftp://x.example", "x.example", "https://"])
def test_normalize_rejects_non_http_urls(bad):
    with pytest.raises(immich_client.ImmichError):
        immich_client.normalize_base_url(bad)


# --- error mapping -----------------------------------------------------------

def _status_error(code):
    req = httpx.Request("GET", "https://x.example/api/albums")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(code, request=req))


def test_auth_errors_map_to_invalid_api_key():
    assert "Invalid API key" in str(immich_client._raise_friendly(_status_error(401)))
    assert "Invalid API key" in str(immich_client._raise_friendly(_status_error(403)))
    assert "HTTP 500" in str(immich_client._raise_friendly(_status_error(500)))


# --- iter_assets pagination ---------------------------------------------------

def _paged_transport(pages, seen_bodies):
    """MockTransport yielding canned /search/metadata pages by cursor."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/search/metadata"  # relative-path join preserved /api
        body = json.loads(request.content)
        seen_bodies.append(body)
        items, next_page = pages[body["page"]]
        return httpx.Response(200, json={"assets": {"items": items, "nextPage": next_page}})

    return httpx.MockTransport(handler)


@pytest.fixture
def inject_transport(monkeypatch):
    """Route immich_client's internally-built clients through a MockTransport."""

    def _inject(transport):
        orig = immich_client._client

        def patched(*args, **kwargs):
            kwargs["transport"] = transport
            return orig(*args, **kwargs)

        monkeypatch.setattr(immich_client, "_client", patched)

    return _inject


async def test_iter_assets_follows_cursor_and_filters_images(inject_transport):
    pages = {
        1: ([{"id": "a", "type": "IMAGE"}, {"id": "v", "type": "VIDEO"}], "2"),
        "2": ([{"id": "b", "type": "IMAGE"}], None),
    }
    seen: list[dict] = []
    inject_transport(_paged_transport(pages, seen))

    got = [
        a["id"]
        async for a in immich_client.iter_assets(
            "https://x.example", "key", album_ids=["alb1"], verify=True
        )
    ]

    assert got == ["a", "b"]  # video filtered out; both pages consumed
    assert [b["page"] for b in seen] == [1, "2"]  # cursor followed verbatim
    assert seen[0]["withExif"] is True and seen[0]["albumIds"] == ["alb1"]


async def test_iter_assets_http_error_becomes_immich_error(inject_transport):
    inject_transport(httpx.MockTransport(lambda req: httpx.Response(500)))
    with pytest.raises(immich_client.ImmichError, match="HTTP 500"):
        async for _ in immich_client.iter_assets("https://x.example", "key", album_ids=["alb1"]):
            pass


# --- download_original atomicity ----------------------------------------------

async def test_download_writes_atomically(tmp_path):
    payload = b"jpeg-bytes" * 1000

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=payload)
    )
    dest = tmp_path / "asset.jpg"
    async with httpx.AsyncClient(transport=transport, base_url="https://x.example/api/") as client:
        await immich_client.download_original(client, "asset-1", dest)

    assert dest.read_bytes() == payload
    assert not (tmp_path / "asset.jpg.part").exists()


async def test_failed_download_leaves_no_file_behind(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    dest = tmp_path / "asset.jpg"
    async with httpx.AsyncClient(transport=transport, base_url="https://x.example/api/") as client:
        with pytest.raises(httpx.HTTPStatusError):
            await immich_client.download_original(client, "asset-1", dest)

    # Neither a complete-looking file nor a stale partial may remain.
    assert not dest.exists()
    assert not (tmp_path / "asset.jpg.part").exists()

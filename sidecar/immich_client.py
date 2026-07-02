"""Async Immich REST client for pulling photos into Ash.

Talks to a self-hosted Immich server's ``/api`` using an API key (``x-api-key``).
Kept deliberately small: enough to validate a connection, list albums, enumerate
the IMAGE assets of selected albums (with the server-side EXIF Immich already
computed), and download originals.

Credentials are passed per-call rather than read from the environment, so the
Electron app can connect and import at runtime without restarting the sidecar.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx

# Metadata calls are quick; a full-res original can be tens of MB, so give
# downloads a much longer read budget.
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0)

_PAGE_SIZE = 250


class ImmichError(Exception):
    """A human-readable Immich API failure, safe to surface in the UI."""


def normalize_base_url(base_url: str) -> str:
    """Return the server root (``scheme://host[:port]``) with any trailing ``/``
    or ``/api`` stripped. Raises :class:`ImmichError` on a non-http(s) URL."""
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        raise ImmichError("Server URL is required.")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ImmichError("Server URL must start with http:// or https://")
    if raw.endswith("/api"):
        raw = raw[: -len("/api")]
    return raw


def _client(
    base_url: str,
    api_key: str,
    *,
    verify: bool = True,
    timeout: httpx.Timeout = _TIMEOUT,
) -> httpx.AsyncClient:
    # base_url ends with "/api/" and every request path is RELATIVE (no leading
    # slash) — an absolute "/foo" path would drop the "/api" prefix under RFC
    # 3986 URL joining.
    return httpx.AsyncClient(
        base_url=f"{normalize_base_url(base_url)}/api/",
        headers={"x-api-key": api_key, "Accept": "application/json"},
        timeout=timeout,
        verify=verify,
        follow_redirects=True,
    )


def _raise_friendly(exc: httpx.HTTPError) -> "ImmichError":
    """Translate an httpx error into a UI-safe ImmichError."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return ImmichError("Invalid API key.")
        return ImmichError(f"Server returned HTTP {code}.")
    return ImmichError(f"Cannot reach server: {exc}")


async def test_connection(base_url: str, api_key: str, *, verify: bool = True) -> dict[str, Any]:
    """Validate creds by asking the server about itself. Returns its JSON (with a
    ``version``). Raises :class:`ImmichError` with a friendly message on failure."""
    async with _client(base_url, api_key, verify=verify) as client:
        try:
            resp = await client.get("server/about")
            if resp.status_code == 404:  # older servers only expose /server/version
                resp = await client.get("server/version")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise _raise_friendly(exc) from exc


async def list_albums(base_url: str, api_key: str, *, verify: bool = True) -> list[dict[str, Any]]:
    """GET /api/albums → list of album dicts (``id``, ``albumName``, ``assetCount``…)."""
    async with _client(base_url, api_key, verify=verify) as client:
        try:
            resp = await client.get("albums")
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise _raise_friendly(exc) from exc
    return data if isinstance(data, list) else []


async def iter_assets(
    base_url: str,
    api_key: str,
    *,
    album_ids: list[str],
    verify: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Yield IMAGE asset DTOs (each carrying ``exifInfo``) across the given albums.

    Enumerates via POST /api/search/metadata — paginated, ``withExif: true`` —
    rather than GET /api/albums/{id}: the album endpoint returns every asset in a
    single unbounded response and its EXIF inclusion is not guaranteed. Follows
    the response's ``nextPage`` cursor until it is null (no assumption about 0- vs
    1-indexed pages).
    """
    if not album_ids:
        return
    async with _client(base_url, api_key, verify=verify) as client:
        page: Any = 1
        while page:
            body = {
                "albumIds": album_ids,
                "type": "IMAGE",
                "withExif": True,
                "size": _PAGE_SIZE,
                "page": page,
            }
            try:
                resp = await client.post("search/metadata", json=body)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as exc:
                raise _raise_friendly(exc) from exc
            assets = data.get("assets") or {}
            for item in assets.get("items") or []:
                if item.get("type") == "IMAGE":
                    yield item
            page = assets.get("nextPage")  # str | int | None


async def download_original(client: httpx.AsyncClient, asset_id: str, dest: Path) -> None:
    """Stream GET /api/assets/{id}/original to ``dest`` atomically.

    Writes to a ``.part`` sibling and ``os.replace``s into place only after the
    full body arrives; removes the partial on any error so a failed download never
    leaves a file that later looks complete.
    """
    tmp = dest.parent / (dest.name + ".part")
    try:
        async with client.stream("GET", f"assets/{asset_id}/original") as resp:
            resp.raise_for_status()
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes(1 << 16):
                    f.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

"""FastAPI sidecar: ingest photos into khora and serve the gallery/search API.

Runs on 127.0.0.1 only. The Electron app spawns this process, waits on
``/health``, and proxies the renderer's requests here.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pycountry
import reverse_geocoder as rg

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps
from pydantic import BaseModel

import khora_client as kc
from expertise import ENTITY_TYPES, PHOTO_EXPERTISE, RELATIONSHIP_TYPES
from vision import describe_image

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
INGEST_CONCURRENCY = 4
THUMB_SIZE = (512, 512)
# Relevance floor for semantic search (raw cosine, applied before khora
# normalizes scores into a [0,1] rank). 0 disables it. Default 0.5 is
# calibrated for the bundled nomic-embed-text-v1.5 model: on a real ingest it
# kept loose-but-valid matches ("pet" -> the cat, "seaside" -> the beach) while
# dropping nonsense queries and unrelated images (0.3 was a no-op — nomic's
# baseline cosine is high; 0.6 started cutting valid fuzzy matches). Override
# via PHOTO_MIN_SIMILARITY for other embedding models.
MIN_SIMILARITY = float(os.environ.get("PHOTO_MIN_SIMILARITY", "0.5") or 0)

# Graph-augmented search: fold the entity graph into ranking. The query is also
# run through khora's entity vector search; photos that are a source of a
# query-matching entity (e.g. "castle" -> the PLACE node linking 4 photos) are
# fused with the recall results. This surfaces connected photos whose own
# description matched weakly. Set PHOTO_GRAPH_SEARCH=0 to disable.
GRAPH_SEARCH = os.environ.get("PHOTO_GRAPH_SEARCH", "1") not in ("0", "false", "False", "")
GRAPH_ENTITY_LIMIT = int(os.environ.get("PHOTO_GRAPH_ENTITY_LIMIT", "8"))
GRAPH_WEIGHT = float(os.environ.get("PHOTO_GRAPH_WEIGHT", "1.0") or 0)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    await kc.startup()
    _load_usage()
    yield
    await kc.shutdown()


app = FastAPI(title="photo-gallery sidecar", lifespan=_lifespan)

# Per-session shared secret, minted by the Electron main process and handed to
# both this sidecar (env) and the renderer (IPC). Every request must present it.
# Empty when launched standalone (e.g. `uvicorn server:app` for dev) — then the
# gate is disabled, matching the loopback-only, single-user assumption.
SIDECAR_TOKEN = os.environ.get("PHOTO_SIDECAR_TOKEN", "")


@app.middleware("http")
async def _require_token(request: Request, call_next):
    # The renderer sends the token as a header on fetch() and as a ?token= query
    # param on <img> URLs (image elements can't set custom headers). Preflight
    # (OPTIONS) carries neither and must pass through to CORS handling.
    if SIDECAR_TOKEN and request.method != "OPTIONS":
        supplied = request.headers.get("x-ash-token") or request.query_params.get("token") or ""
        if not hmac.compare_digest(supplied, SIDECAR_TOKEN):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


# CORS must stay permissive: the renderer is a file:// page, so every request to
# this http://127.0.0.1 service is cross-origin (Origin: null). The access
# boundary is the token above, not the origin — a page that lacks the token gets
# 401 regardless of whether it could read the response.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory ingest job tracking (single-user desktop app).
_jobs: dict[str, dict[str, Any]] = {}
# Cap retained finished jobs so a long-lived process doesn't leak their (often
# large) per-file status maps. Running jobs are never evicted.
MAX_RETAINED_JOBS = 32


def _register_job(job_id: str, job: dict[str, Any]) -> None:
    _jobs[job_id] = job
    finished = [jid for jid, j in _jobs.items() if j.get("status") == "done"]
    for jid in finished[: max(0, len(finished) - MAX_RETAINED_JOBS)]:  # oldest first (insertion order)
        _jobs.pop(jid, None)

# ---------------------------------------------------------------------------
# OpenAI spend tracking — measured token tallies, persisted per library.
# Prices are USD per 1M tokens (input, output); approximate, edit as needed.
# ---------------------------------------------------------------------------
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}
_DEFAULT_PRICE = (0.15, 0.60)
_USAGE_FILE = kc.DATA_DIR / "usage.json"
_usage: dict[str, Any] = {"by_model": {}, "photos": 0}


def _load_usage() -> None:
    global _usage
    try:
        loaded = json.loads(_USAGE_FILE.read_text())
        _usage = loaded if isinstance(loaded, dict) else {}  # tolerate a corrupt/non-dict file
    except Exception:
        _usage = {}
    _usage.setdefault("by_model", {})
    _usage.setdefault("photos", 0)


def _persist_usage() -> None:
    try:
        _USAGE_FILE.write_text(json.dumps(_usage))
    except Exception:
        pass


def _record_usage(model: str, inp: int, out: int) -> None:
    m = _usage["by_model"].setdefault(model, {"input": 0, "output": 0})
    m["input"] += int(inp or 0)
    m["output"] += int(out or 0)


def _price_for(model: str) -> tuple[float, float]:
    for key, rates in PRICING.items():
        if key in model:
            return rates
    return _DEFAULT_PRICE


def _estimated_cost() -> float:
    total = 0.0
    for model, tok in _usage["by_model"].items():
        in_rate, out_rate = _price_for(model)
        total += tok["input"] / 1_000_000 * in_rate + tok["output"] / 1_000_000 * out_rate
    return total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _photo_datetime(path: Path) -> datetime:
    """EXIF DateTimeOriginal if present, else file mtime. Always tz-aware UTC."""
    try:
        exif = Image.open(path).getexif()
        ifd = exif.get_ifd(0x8769)  # Exif IFD
        raw = ifd.get(36867) or ifd.get(36868) or exif.get(306)
        if raw:
            return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _photo_gps(path: Path) -> tuple[float, float] | None:
    """Return (latitude, longitude) from EXIF GPS IFD, or None if absent."""
    try:
        exif = Image.open(path).getexif()
        gps = exif.get_ifd(0x8825)  # GPS IFD
        if not gps:
            return None
        lat_ref = gps.get(1)   # 'N' or 'S'
        lat_dms = gps.get(2)   # (degrees, minutes, seconds)
        lon_ref = gps.get(3)   # 'E' or 'W'
        lon_dms = gps.get(4)
        if not (lat_ref and lat_dms and lon_ref and lon_dms):
            return None
        def to_deg(dms, ref: str) -> float:
            d, m, s = (float(x) for x in dms)
            deg = d + m / 60 + s / 3600
            return -deg if ref in ("S", "W") else deg
        lat = to_deg(lat_dms, lat_ref)
        lon = to_deg(lon_dms, lon_ref)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return lat, lon
    except Exception:
        return None


def _reverse_geocode(lat: float, lon: float) -> dict[str, str]:
    """Return {city, admin1, country} for coordinates using local GeoNames data."""
    try:
        results = rg.search((lat, lon), verbose=False)
        if results:
            r = results[0]
            cc = r.get("cc", "")
            c = pycountry.countries.get(alpha_2=cc)
            country = c.name if c else cc
            return {
                "gps_city": r.get("name", ""),
                "gps_admin1": r.get("admin1", ""),
                "gps_country": country,
                "gps_country_code": cc,
            }
    except Exception:
        pass
    return {}


def _make_thumbnail(path: Path, ext_id: str) -> None:
    out = kc.THUMBS_DIR / f"{ext_id}.webp"
    if out.exists():
        return
    img = ImageOps.exif_transpose(Image.open(path))
    img.thumbnail(THUMB_SIZE)
    img.convert("RGB").save(out, "WEBP", quality=80)


def _custom(meta: dict[str, Any] | None) -> dict[str, Any]:
    return (meta or {}).get("custom", {}) or {}


def _parse_uuid(doc_id: str) -> uuid.UUID:
    """Parse a path-segment doc id, returning 404 (not an unhandled 500) if malformed."""
    try:
        return uuid.UUID(doc_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, "not found")


def _photo_dto(doc: Any, *, description: str | None = None, score: float | None = None) -> dict[str, Any]:
    """Map a Document or DocumentProjection to the gallery DTO (defensive getattr)."""
    meta = getattr(doc, "metadata", None) or {}
    c = _custom(meta)
    ext = getattr(doc, "external_id", None)
    st = getattr(doc, "source_timestamp", None)
    return {
        "id": str(getattr(doc, "id")),
        "external_id": ext,
        "thumb_url": f"/thumb/{ext}" if ext else None,
        "src": getattr(doc, "source_url", None),
        "title": getattr(doc, "title", None),
        "description": description if description is not None else (getattr(doc, "content", "") or ""),
        "location": c.get("location") or None,
        "objects": c.get("objects", []),
        "animals": c.get("animals", []),
        "scene": c.get("scene") or None,
        "tags": c.get("tags", []),
        "occurred_at": c.get("occurred_at") or (st.isoformat() if st else None),
        "gps_lat": c.get("gps_lat"),
        "gps_lon": c.get("gps_lon"),
        "gps_city": c.get("gps_city"),
        "gps_admin1": c.get("gps_admin1"),
        "gps_country": c.get("gps_country"),
        "gps_country_code": c.get("gps_country_code"),
        "score": score,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        ns = kc.namespace()
    except RuntimeError:
        raise HTTPException(503, "starting")
    return {"status": "ok", "namespace": str(ns), "has_openai_key": bool(os.environ.get("OPENAI_API_KEY"))}


@app.get("/usage")
async def usage() -> dict[str, Any]:
    by = _usage["by_model"]
    return {
        "cost_usd": round(_estimated_cost(), 4),
        "input_tokens": sum(m["input"] for m in by.values()),
        "output_tokens": sum(m["output"] for m in by.values()),
        "photos": _usage["photos"],
    }


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------
class IngestRequest(BaseModel):
    paths: list[str]


def _expand_paths(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            out.extend(f for f in path.rglob("*") if f.suffix.lower() in IMAGE_EXTS)
        elif path.suffix.lower() in IMAGE_EXTS and path.is_file():
            out.append(path)
    # de-dupe while preserving order
    seen: set[str] = set()
    uniq: list[Path] = []
    for f in out:
        resolved = f.resolve()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            uniq.append(resolved)
    return uniq


@app.post("/ingest")
async def ingest(req: IngestRequest) -> dict[str, Any]:
    files = _expand_paths(req.paths)
    job_id = uuid.uuid4().hex
    _register_job(job_id, {
        "total": len(files),
        "done": 0,
        "skipped": 0,
        "failed": 0,
        "status": "running",
        "errors": [],
        "items": {str(f): {"status": "pending"} for f in files},  # per-file status
    })
    asyncio.create_task(_run_ingest(job_id, files))
    return {"job_id": job_id, "paths": [str(f) for f in files]}


@app.get("/ingest/status")
async def ingest_status(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


async def _run_ingest(job_id: str, files: list[Path]) -> None:
    job = _jobs[job_id]
    # Idempotency: external_id == file content hash; skip already-ingested files.
    existing = {d.external_id for d in await kc.kb().list_documents(namespace=kc.namespace(), limit=100000)}
    sem = asyncio.Semaphore(INGEST_CONCURRENCY)

    async def one(path: Path) -> None:
        key = str(path)
        async with sem:
            try:
                job["items"][key] = {"status": "scanning"}
                ext_id = _hash_file(path)
                if ext_id in existing:
                    job["skipped"] += 1
                    job["items"][key] = {"status": "skipped", "ext_id": ext_id}
                    return
                dt = _photo_datetime(path)
                gps = _photo_gps(path)
                geo = _reverse_geocode(gps[0], gps[1]) if gps else {}
                desc, vusage = await describe_image(path)
                _record_usage(vusage["model"], vusage["input"], vusage["output"])
                await asyncio.to_thread(_make_thumbnail, path, ext_id)
                place_parts = [geo.get("gps_city"), geo.get("gps_admin1"), geo.get("gps_country"), geo.get("gps_country_code")]
                place_suffix = ", ".join(p for p in place_parts if p)
                base_content = desc["description"] or f"Photo: {path.name}"
                content = f"{base_content} [{place_suffix}]" if place_suffix else base_content
                result = await kc.kb().remember(
                    content=content,
                    namespace=kc.namespace(),
                    title=path.name,
                    source_type="photo",
                    source_url=str(path.resolve()),
                    source_timestamp=dt,
                    external_id=ext_id,
                    metadata={
                        "custom": {
                            "location": desc["location"],
                            "objects": desc["objects"],
                            "animals": desc["animals"],
                            "scene": desc["scene"],
                            "tags": desc["tags"],
                            "occurred_at": dt.isoformat(),
                            "filename": path.name,
                            "gps_lat": gps[0] if gps else None,
                            "gps_lon": gps[1] if gps else None,
                            **geo,
                        }
                    },
                    entity_types=ENTITY_TYPES,
                    relationship_types=RELATIONSHIP_TYPES,
                    expertise=PHOTO_EXPERTISE,
                )
                for u in result.llm_usage:  # extraction + embedding tokens from khora
                    _record_usage(u.model, u.prompt_tokens, u.completion_tokens)
                _usage["photos"] += 1
                _persist_usage()
                existing.add(ext_id)
                job["done"] += 1
                job["items"][key] = {
                    "status": "done",
                    "ext_id": ext_id,
                    "doc_id": str(result.document_id),
                }
            except Exception as exc:  # keep the batch going; record the failure
                msg = f"{type(exc).__name__}: {exc}"
                job["failed"] += 1
                job["errors"].append({"path": str(path), "error": msg})
                job["items"][key] = {"status": "failed", "error": msg}

    await asyncio.gather(*(one(f) for f in files))
    job["status"] = "done"


# ---------------------------------------------------------------------------
# Rescan — re-run the models over the existing library (e.g. after a model swap).
#   describe: re-run the vision model on every photo, then re-extract.
#   extract:  re-run only entity extraction over the stored descriptions.
# ---------------------------------------------------------------------------
class RescanRequest(BaseModel):
    mode: str = "describe"


@app.post("/rescan")
async def rescan(req: RescanRequest) -> dict[str, Any]:
    mode = req.mode if req.mode in ("describe", "extract") else "describe"
    docs = await kc.kb().list_documents(namespace=kc.namespace(), limit=100000)
    items = [
        {
            "doc_id": d.id,
            "external_id": getattr(d, "external_id", None),
            "title": getattr(d, "title", None),
            "source_url": getattr(d, "source_url", None),
            "source_timestamp": getattr(d, "source_timestamp", None),
            "content": getattr(d, "content", "") or "",
            "metadata": getattr(d, "metadata", None) or {},
        }
        for d in docs
    ]
    job_id = uuid.uuid4().hex
    _register_job(job_id, {"total": len(items), "done": 0, "skipped": 0, "failed": 0, "status": "running", "errors": []})
    asyncio.create_task(_run_rescan(job_id, items, mode))
    return {"job_id": job_id, "total": len(items), "mode": mode}


async def _run_rescan(job_id: str, items: list[dict[str, Any]], mode: str) -> None:
    job = _jobs[job_id]
    ns = kc.namespace()
    sem = asyncio.Semaphore(INGEST_CONCURRENCY)

    async def one(it: dict[str, Any]) -> None:
        async with sem:
            try:
                if mode == "describe":
                    src = it["source_url"]
                    path = Path(src) if src else None
                    if path is None or not path.exists():
                        job["skipped"] += 1  # original file gone — leave the doc as-is
                        return
                    ext_id = it["external_id"] or _hash_file(path)
                    dt = it["source_timestamp"] or _photo_datetime(path)
                    gps = _photo_gps(path)
                    geo = _reverse_geocode(gps[0], gps[1]) if gps else {}
                    # Describe first; if it raises we keep the existing doc (no data loss).
                    desc, vusage = await describe_image(path)
                    _record_usage(vusage["model"], vusage["input"], vusage["output"])
                    await asyncio.to_thread(_make_thumbnail, path, ext_id)
                    place_parts = [geo.get("gps_city"), geo.get("gps_admin1"), geo.get("gps_country")]
                    place_suffix = ", ".join(p for p in place_parts if p)
                    base_content = desc["description"] or f"Photo: {path.name}"
                    content = f"{base_content} [{place_suffix}]" if place_suffix else base_content
                    await kc.kb().forget(it["doc_id"], namespace=ns)
                    result = await kc.kb().remember(
                        content=content,
                        namespace=ns,
                        title=it["title"] or path.name,
                        source_type="photo",
                        source_url=str(path.resolve()),
                        source_timestamp=dt,
                        external_id=ext_id,
                        metadata={
                            "custom": {
                                "location": desc["location"],
                                "objects": desc["objects"],
                                "animals": desc["animals"],
                                "scene": desc["scene"],
                                "tags": desc["tags"],
                                "occurred_at": dt.isoformat() if hasattr(dt, "isoformat") else str(dt),
                                "filename": path.name,
                                "gps_lat": gps[0] if gps else None,
                                "gps_lon": gps[1] if gps else None,
                                **geo,
                            }
                        },
                        entity_types=ENTITY_TYPES,
                        relationship_types=RELATIONSHIP_TYPES,
                        expertise=PHOTO_EXPERTISE,
                    )
                else:  # extract: rebuild the entity graph from the stored description
                    await kc.kb().forget(it["doc_id"], namespace=ns)
                    result = await kc.kb().remember(
                        content=it["content"] or f"Photo: {it['title'] or ''}",
                        namespace=ns,
                        title=it["title"],
                        source_type="photo",
                        source_url=it["source_url"],
                        source_timestamp=it["source_timestamp"],
                        external_id=it["external_id"],
                        metadata=it["metadata"],
                        entity_types=ENTITY_TYPES,
                        relationship_types=RELATIONSHIP_TYPES,
                        expertise=PHOTO_EXPERTISE,
                    )
                for u in result.llm_usage:
                    _record_usage(u.model, u.prompt_tokens, u.completion_tokens)
                _persist_usage()
                job["done"] += 1
            except Exception as exc:
                job["failed"] += 1
                job["errors"].append({"doc_id": str(it["doc_id"]), "error": f"{type(exc).__name__}: {exc}"})

    await asyncio.gather(*(one(it) for it in items))
    job["status"] = "done"


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
def _build_filter(
    location: str | None,
    scene: str | None,
    objects: list[str],
    tags: list[str],
    date_from: str | None,
    date_to: str | None,
    city: str | None = None,
    country: str | None = None,
) -> dict[str, Any] | None:
    f: dict[str, Any] = {}
    if location:
        f["metadata.custom.location"] = location
    if scene:
        f["metadata.custom.scene"] = scene
    if objects:
        f["metadata.custom.objects"] = {"$in": objects}
    if tags:
        f["metadata.custom.tags"] = {"$in": tags}
    if date_from or date_to:
        rng: dict[str, str] = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        f["metadata.custom.occurred_at"] = rng
    if city:
        f["metadata.custom.gps_city"] = city
    if country:
        f["metadata.custom.gps_country"] = country
    return f or None


def _py_match(
    c: dict[str, Any],
    location: str | None,
    scene: str | None,
    objects: list[str],
    tags: list[str],
    date_from: str | None,
    date_to: str | None,
    city: str | None = None,
    country: str | None = None,
) -> bool:
    if location and (c.get("location") or "").lower() != location.lower():
        return False
    if scene and (c.get("scene") or "").lower() != scene.lower():
        return False
    if objects and not {o.lower() for o in c.get("objects", [])} & {o.lower() for o in objects}:
        return False
    if tags and not {t.lower() for t in c.get("tags", [])} & {t.lower() for t in tags}:
        return False
    oa = c.get("occurred_at") or ""
    if date_from and oa < date_from:
        return False
    if date_to and oa > date_to:
        return False
    if city and (c.get("gps_city") or "").lower() != city.lower():
        return False
    if country and (c.get("gps_country") or "").lower() != country.lower():
        return False
    return True


def _rrf(rankings: list[tuple[list[str], float]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal-rank-fuse several ranked id lists into one ranking.

    Each input is (ordered_ids, weight). A doc's score is the weighted sum of
    1/(k+rank) across the lists it appears in, so docs ranked highly by *both*
    vector recall and the entity graph rise to the top. Scale-free — no need to
    reconcile cosine vs rank units. Returns (id, score) sorted best-first."""
    scores: dict[str, float] = {}
    for ids, weight in rankings:
        for rank, key in enumerate(ids):
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


async def _entity_doc_ranking(q: str, ns: Any) -> list[str]:
    """Rank document ids by the entity graph: find entities matching the query
    (vector search over entity embeddings), then list the photos that are their
    sources, best-matching-entity first."""
    try:
        ents = await kc.kb().search_entities(q, namespace=ns, limit=GRAPH_ENTITY_LIMIT, include_sources=True)
    except Exception:
        return []
    order: list[str] = []
    seen: set[str] = set()
    for ent in ents:
        for did in getattr(ent, "source_document_ids", None) or []:
            key = str(did)
            if key not in seen:
                seen.add(key)
                order.append(key)
    return order


@app.get("/gallery")
async def gallery(limit: int = 500) -> dict[str, Any]:
    docs = await kc.kb().list_documents(namespace=kc.namespace(), limit=limit)
    photos = [_photo_dto(d) for d in docs]
    photos.sort(key=lambda p: p["occurred_at"] or "", reverse=True)
    return {"photos": photos, "mode": "gallery"}


@app.get("/search")
async def search(
    q: str = "",
    location: str | None = None,
    scene: str | None = None,
    objects: list[str] = Query(default=[]),
    tags: list[str] = Query(default=[]),
    date_from: str | None = None,
    date_to: str | None = None,
    city: str | None = None,
    country: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    q = q.strip()

    if q:
        flt = _build_filter(location, scene, objects, tags, date_from, date_to, city, country)
        # Relevance floor (MIN_SIMILARITY) keeps the result set to photos that
        # actually relate to the query instead of returning the whole library
        # ranked. Passed only when >0 so a 0 override restores "return all".
        ns = kc.namespace()
        recall_kwargs: dict[str, Any] = {}
        if MIN_SIMILARITY > 0:
            recall_kwargs["min_similarity"] = MIN_SIMILARITY
        result = await kc.kb().recall(q, namespace=ns, limit=limit, filter=flt, **recall_kwargs)

        # Recall ranking: one entry per document, chunks are score-sorted.
        docs: dict[str, Any] = {str(d.id): d for d in result.documents}
        chunk_for: dict[str, Any] = {}
        recall_order: list[str] = []
        for ch in result.chunks:
            key = str(ch.document_id)
            if key not in chunk_for:
                chunk_for[key] = ch
                recall_order.append(key)

        # Graph leg: photos sharing an entity that matches the query, fused in.
        graph_order = await _entity_doc_ranking(q, ns) if GRAPH_SEARCH else []
        if graph_order:
            fused = _rrf([(recall_order, 1.0), (graph_order, GRAPH_WEIGHT)])
        else:
            fused = [(key, 1.0 / (60 + i)) for i, key in enumerate(recall_order)]

        # Display score = relative rank within the fused set (top = 1.0); the bar
        # in the UI reads it as "ranked against the others", not absolute match.
        hi = fused[0][1] if fused else 1.0
        lo = fused[-1][1] if fused else 0.0
        span = (hi - lo) or 1.0
        mode = "semantic+graph" if graph_order else "semantic"

        photos = []
        for key, raw in fused:
            doc = docs.get(key)
            if doc is None:  # graph-only hit: not returned by recall, fetch it
                try:
                    doc = await kc.kb().get_document(uuid.UUID(key), namespace=ns)
                except Exception:
                    doc = None
                if doc is None:
                    continue
                # graph-injected photos must still honour any active chip filters
                if flt and not _py_match(_custom(getattr(doc, "metadata", None)), location, scene, objects, tags, date_from, date_to, city, country):
                    continue
            ch = chunk_for.get(key)
            photos.append(_photo_dto(doc, description=ch.content if ch else None, score=(raw - lo) / span))
            if len(photos) >= limit:
                break
        return {"photos": photos, "mode": mode}

    # Filter-only browse: enumerate all docs and match in Python (true "show all matching").
    docs_all = await kc.kb().list_documents(namespace=kc.namespace(), limit=100000)
    photos = [
        _photo_dto(d)
        for d in docs_all
        if _py_match(_custom(getattr(d, "metadata", None)), location, scene, objects, tags, date_from, date_to, city, country)
    ]
    photos.sort(key=lambda p: p["occurred_at"] or "", reverse=True)
    return {"photos": photos[:limit], "mode": "browse"}


@app.get("/facets")
async def facets() -> dict[str, list[str]]:
    docs = await kc.kb().list_documents(namespace=kc.namespace(), limit=100000)
    locations: set[str] = set()
    scenes: set[str] = set()
    objects: set[str] = set()
    animals: set[str] = set()
    tags: set[str] = set()
    cities: set[str] = set()
    countries: set[str] = set()
    for d in docs:
        c = _custom(getattr(d, "metadata", None))
        if c.get("location"):
            locations.add(c["location"])
        if c.get("scene"):
            scenes.add(c["scene"])
        objects.update(c.get("objects", []))
        animals.update(c.get("animals", []))
        tags.update(c.get("tags", []))
        if c.get("gps_city"):
            cities.add(c["gps_city"])
        if c.get("gps_country"):
            countries.add(c["gps_country"])
    return {
        "location": sorted(locations),
        "scene": sorted(scenes),
        "objects": sorted(objects),
        "animals": sorted(animals),
        "tags": sorted(tags),
        "cities": sorted(cities),
        "countries": sorted(countries),
    }


_TYPE_LABELS = {"ANIMAL": "Animals", "PLACE": "Places", "OBJECT": "Objects", "SCENE": "Scenes"}


@app.get("/themes")
async def themes(limit_per_theme: int = 60) -> dict[str, Any]:
    """Group photos into themes, each carrying its member photos (with thumbnails).

    Two kinds: category groups (Animals/Places/Objects/Scenes — always useful) and
    finer recurring-entity themes (the same specific subject across 2+ photos)."""
    ns = kc.namespace()
    docs = await kc.kb().list_documents(namespace=ns, limit=100000)
    docmap = {str(d.id): d for d in docs}

    def photos_for(ids: set[str]) -> list[dict[str, Any]]:
        return [_photo_dto(docmap[i]) for i in list(ids)[:limit_per_theme] if i in docmap]

    ents_by_type = {
        t: await kc.kb().list_entities(namespace=ns, entity_type=t, limit=2000, include_sources=True)
        for t in ENTITY_TYPES
    }

    themes: list[dict[str, Any]] = []
    # Category groups first (>=2 photos) — the reliable, always-populated view.
    for t in ENTITY_TYPES:
        ids: set[str] = set()
        for e in ents_by_type[t]:
            ids.update(str(x) for x in e.source_document_ids)
        if len(ids) >= 2:
            themes.append({"label": _TYPE_LABELS[t], "type": t, "kind": "category", "count": len(ids), "photos": photos_for(ids)})
    # Finer recurring-entity themes (same specific subject in 2+ photos).
    entity_themes: list[dict[str, Any]] = []
    for t in ENTITY_TYPES:
        for e in ents_by_type[t]:
            ids = {str(x) for x in e.source_document_ids}
            if len(ids) >= 2:
                entity_themes.append({"label": e.name, "type": t, "kind": "entity", "count": len(ids), "photos": photos_for(ids)})
    entity_themes.sort(key=lambda x: -x["count"])
    themes.extend(entity_themes)
    return {"themes": themes}


@app.get("/related/{doc_id}")
async def related(doc_id: str, limit: int = 12) -> dict[str, Any]:
    from collections import Counter

    target = _parse_uuid(doc_id)
    counts: Counter[Any] = Counter()
    for t in ENTITY_TYPES:
        for e in await kc.kb().list_entities(
            namespace=kc.namespace(), entity_type=t, limit=1000, include_sources=True
        ):
            ids = set(e.source_document_ids)
            if target in ids:
                for other in ids:
                    if other != target:
                        counts[other] += 1
    photos: list[dict[str, Any]] = []
    for other_id, shared in counts.most_common(limit):
        doc = await kc.kb().get_document(other_id, namespace=kc.namespace())
        if doc is not None:
            dto = _photo_dto(doc)
            dto["shared_entities"] = shared
            photos.append(dto)
    return {"photos": photos}


@app.get("/photo/{doc_id}")
async def photo(doc_id: str) -> dict[str, Any]:
    doc = await kc.kb().get_document(_parse_uuid(doc_id), namespace=kc.namespace())
    if doc is None:
        raise HTTPException(404, "not found")
    dto = _photo_dto(doc)
    dto["exists_on_disk"] = bool(dto["src"]) and Path(dto["src"]).exists()
    return dto


@app.get("/image/{doc_id}")
async def image(doc_id: str) -> FileResponse:
    doc = await kc.kb().get_document(_parse_uuid(doc_id), namespace=kc.namespace())
    if doc is None or not doc.source_url:
        raise HTTPException(404, "not found")
    p = Path(doc.source_url)
    if not p.exists():
        raise HTTPException(410, "original file missing")
    return FileResponse(p)


@app.get("/thumb/{ext_id}")
async def thumb(ext_id: str) -> FileResponse:
    safe = "".join(ch for ch in ext_id if ch in "0123456789abcdef")  # hash only
    out = kc.THUMBS_DIR / f"{safe}.webp"
    if not out.exists():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(out, media_type="image/webp")

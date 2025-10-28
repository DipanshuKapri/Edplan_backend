
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Annotated, Optional, List, Dict, Any, Set, Callable, Awaitable

import hashlib
import json
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from scorecard_client import ScorecardClient, DEFAULT_FIELDS, DETAIL_FIELDS, PROGRAM_FIELDS
from utils import csv_to_set

APP_NAME = "EdPlan College Guide Backend"
APP_VERSION = "1.0.0"
START_TIME = datetime.utcnow()
REQUEST_ID_HEADER = "x-request-id"


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    allowed_origins: Set[str] = Field(default_factory=set)
    allowed_hosts: Set[str] = Field(default_factory=set)
    cache_ttl_seconds: int = 900
    ready_cache_seconds: int = 60
    log_level: str = "INFO"
    cors_allow_credentials: bool = False
    public_cache_seconds: int = 60
    programs_public_cache_seconds: int = 300

    @classmethod
    def load(cls) -> "Settings":
        allowed_origins = csv_to_set(os.getenv("ALLOWED_ORIGINS"))
        allowed_hosts = csv_to_set(os.getenv("ALLOWED_HOSTS"))
        if not allowed_hosts:
            allowed_hosts = {"localhost", "127.0.0.1"}
        return cls(
            allowed_origins=allowed_origins,
            allowed_hosts=allowed_hosts,
            cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "900")),
            ready_cache_seconds=int(os.getenv("READY_CHECK_CACHE_SECONDS", "60")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            cors_allow_credentials=_env_bool(os.getenv("CORS_ALLOW_CREDENTIALS"), False),
            public_cache_seconds=int(os.getenv("PUBLIC_CACHE_SECONDS", "60")),
            programs_public_cache_seconds=int(os.getenv("PROGRAMS_PUBLIC_CACHE_SECONDS", "300")),
        )


settings = Settings.load()
LOG_LEVEL = getattr(logging, settings.log_level, logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("edplan.backend")
logger.setLevel(LOG_LEVEL)

app = FastAPI(title=APP_NAME, version=APP_VERSION)

if settings.allowed_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(settings.allowed_hosts),
    )

cors_origins = list(settings.allowed_origins or {"http://localhost:5173", "http://127.0.0.1:5173"})
allow_credentials = settings.cors_allow_credentials and bool(settings.allowed_origins)
if "*" in cors_origins and allow_credentials:
    logger.warning("CORS_ALLOW_CREDENTIALS cannot be used with wildcard origins; disabling credentials.")
    allow_credentials = False
if not settings.allowed_origins:
    logger.info("Using default CORS origins for local development: %s", cors_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=500)

client = ScorecardClient()

cache: TTLCache[str, Any] | None
if settings.cache_ttl_seconds > 0:
    cache = TTLCache(maxsize=512, ttl=settings.cache_ttl_seconds)
    logger.info("Response cache enabled with ttl=%s seconds", settings.cache_ttl_seconds)
else:
    cache = None
    logger.info("Response cache disabled")

async def cached_json(
    request: Request,
    key: str,
    fetch: Callable[[], Awaitable[Any]],
    *,
    max_age: int | None = None,
) -> Response:
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return _respond_cached_json(cached, request, max_age)
    value = await fetch()
    if cache is not None:
        try:
            cache[key] = value
        except Exception:
            pass
    return _respond_cached_json(value, request, max_age)


_ready_state: Dict[str, Any] = {"expires": 0.0, "payload": None}
_ready_lock = asyncio.Lock()


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get(REQUEST_ID_HEADER, str(uuid.uuid4()))
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error for %s %s (request_id=%s)", request.method, request.url.path, request_id)
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers[REQUEST_ID_HEADER] = request_id
    logger.info(
        "Handled %s %s -> %s in %.2fms (request_id=%s)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


def _cache_key(path: str, params: Dict[str, Any]) -> str:
    parts = [path] + [f"{k}={v}" for k, v in sorted(params.items())]
    return "|".join(parts)


def _make_etag(payload: Any) -> str:
    try:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except Exception:
        body = str(payload).encode("utf-8")
    digest = hashlib.sha1(body).hexdigest()
    return f'W/"{digest}"'


def _cache_control_value(seconds: int) -> str:
    return f"public, max-age={seconds}"


def _respond_cached_json(payload: Any, request: Request, max_age: int | None = None) -> Response:
    etag = _make_etag(payload)
    inm = request.headers.get("if-none-match")
    headers = {
        "ETag": etag,
        "Cache-Control": _cache_control_value(max_age if max_age is not None else settings.public_cache_seconds),
    }
    if inm and inm == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(payload, headers=headers)


@app.get("/health", tags=["system"])
async def health():
    now = datetime.utcnow()
    uptime_seconds = int((now - START_TIME).total_seconds())
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": APP_VERSION,
        "time": now.isoformat() + "Z",
        "uptime_seconds": uptime_seconds,
        "cache_entries": len(cache) if cache is not None else 0,
        "cache_ttl": settings.cache_ttl_seconds if cache is not None else 0,
    }


@app.get("/ready", tags=["system"])
async def ready():
    now = time.monotonic()
    cached_payload = _ready_state["payload"]
    if cached_payload is not None and now < _ready_state["expires"]:
        return cached_payload
    async with _ready_lock:
        now = time.monotonic()
        cached_payload = _ready_state["payload"]
        if cached_payload is not None and now < _ready_state["expires"]:
            return cached_payload
        try:
            await client.ping()
        except Exception as exc:
            _ready_state["payload"] = None
            _ready_state["expires"] = now
            logger.exception("Readiness check failed")
            raise HTTPException(status_code=503, detail="Upstream Scorecard API unreachable") from exc
        payload = {
            "status": "ready",
            "service": APP_NAME,
            "version": APP_VERSION,
            "checked_at": datetime.utcnow().isoformat() + "Z",
        }
        _ready_state["payload"] = payload
        _ready_state["expires"] = now + settings.ready_cache_seconds
        return payload


class SearchResponse(BaseModel):
    total: Optional[int] = None
    page: int
    per_page: int
    results: List[Dict[str, Any]]


@app.get("/api/v1/autocomplete")
async def autocomplete(request: Request, q: str = Query(..., description="Partial school name"), per_page: int = Query(10, ge=1, le=100)):
    key = _cache_key("autocomplete", {"q": q, "per_page": per_page})
    async def fetch():
        try:
            return await client.autocomplete(q, per_page=per_page)
        except Exception as e:
            logger.exception("Autocomplete upstream error for query=%s", q)
            raise HTTPException(status_code=502, detail="Scorecard upstream error") from e
    return await cached_json(request, key, fetch)


@app.get("/api/v1/search", response_model=SearchResponse)
async def search_schools(
    request: Request,
    q: Optional[str] = Query(None, description="Fuzzy name contains"),
    state: Optional[str] = Query(None, description="2-letter state code"),
    city: Optional[str] = None,
    ownership: Optional[int] = Query(None, ge=1, le=3, description="1=Public, 2=Private nonprofit, 3=Private for-profit"),
    page: int = Query(0, ge=0),
    per_page: int = Query(25, ge=1, le=100),
    sort: Optional[str] = Query(None, description="Scorecard sort syntax, e.g., latest.student.size:desc"),
    fields: Optional[str] = Query(None, description="Comma-separated Scorecard fields to return"),
):
    params = dict(q=q, state=state, city=city, ownership=ownership, page=page, per_page=per_page, sort=sort, fields=fields)
    key = _cache_key("search", params)
    async def fetch():
        try:
            raw = await client.search_schools(**params)
        except Exception as e:
            logger.exception("Search upstream error", extra={"query": params})
            raise HTTPException(status_code=502, detail="Scorecard upstream error") from e
        md = raw.get("metadata", {})
        return SearchResponse(
            total=md.get("total"),
            page=md.get("page", page),
            per_page=md.get("per_page", per_page),
            results=raw.get("results", []),
        ).model_dump()
    return await cached_json(request, key, fetch)


@app.get("/api/v1/schools/{unit_id}")
async def get_school(request: Request, unit_id: int, fields: Optional[str] = Query(None, description="Comma-separated Scorecard fields")):
    params = {"fields": fields or DETAIL_FIELDS}
    key = _cache_key("details", {"id": unit_id, **params})
    async def fetch():
        try:
            raw = await client.get_school_details(unit_id, fields=fields)
        except Exception as e:
            logger.exception("Details upstream error for unit_id=%s", unit_id)
            raise HTTPException(status_code=502, detail="Scorecard upstream error") from e
        items = raw.get("results", [])
        if not items:
            raise HTTPException(status_code=404, detail="School not found")
        return items[0]
    return await cached_json(request, key, fetch)


@app.get("/api/v1/schools/{unit_id}/programs")
async def get_programs(
    request: Request,
    unit_id: int,
    cip_prefix: Optional[str] = Query(None, description="Filter by CIP-4 prefix (e.g., 11)"),
    fields: Optional[str] = Query(None, description="Comma-separated Scorecard fields"),
    min_share: Optional[float] = Query(None, ge=0, le=1, description="Filter programs by minimum share [0..1]"),
    top_n: Optional[int] = Query(None, ge=1, le=50, description="Return only top N programs by share"),
):
    params = {"cip_prefix": cip_prefix, "fields": fields or PROGRAM_FIELDS, "min_share": min_share, "top_n": top_n}
    key = _cache_key("programs", {"id": unit_id, **{k: v for k, v in params.items() if v is not None}})
    async def fetch():
        try:
            raw = await client.get_programs(unit_id, cip_prefix=cip_prefix, fields=fields)
        except Exception as e:
            logger.exception("Programs upstream error for unit_id=%s", unit_id)
            raise HTTPException(status_code=502, detail="Scorecard upstream error") from e
        if (min_share is not None) or (top_n is not None):
            try:
                results = raw.get("results", [])
                for item in results:
                    programs = item.get("latest", {}).get("programs", {}).get("cip_4_digit")
                    if programs is None:
                        programs = item.get("latest.programs.cip_4_digit")  # type: ignore[index]
                    if isinstance(programs, list):
                        filtered = programs
                        if min_share is not None:
                            filtered = [p for p in filtered if isinstance(p, dict) and p.get("share") is not None and p.get("share") >= min_share]
                        try:
                            filtered.sort(key=lambda p: (p.get("share") if isinstance(p, dict) else 0), reverse=True)
                        except Exception:
                            pass
                        if top_n is not None:
                            filtered = filtered[: top_n]
                        if "latest" in item and isinstance(item["latest"], dict):
                            item.setdefault("latest", {}).setdefault("programs", {})["cip_4_digit"] = filtered
                        item["latest.programs.cip_4_digit"] = filtered
            except Exception:
                logger.exception("Failed to post-process programs filtering for unit_id=%s", unit_id)
        return raw
    return await cached_json(request, key, fetch, max_age=settings.programs_public_cache_seconds)


@app.get("/api/v1/compare")
async def compare(
    request: Request,
    ids: Annotated[str, Query(description="Comma-separated UNITIDs")],
    fields: Optional[str] = Query(None, description="Comma-separated Scorecard fields"),
):
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid id list") from exc
    key = _cache_key("compare", {"ids": ids, "fields": fields or DEFAULT_FIELDS})
    async def fetch():
        try:
            return await client.get_many(id_list, fields=fields)
        except Exception as e:
            logger.exception("Compare upstream error for ids=%s", ids)
            raise HTTPException(status_code=502, detail="Scorecard upstream error") from e
    return await cached_json(request, key, fetch)


@app.on_event("shutdown")
async def shutdown_event():
    await client.close()

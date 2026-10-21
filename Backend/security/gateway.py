"""Internal gateway. Only the CRS proxy is allowed to submit public requests."""
import asyncio
import hmac
import ipaddress
import json
import logging
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from prometheus_client import Counter, Gauge, CONTENT_TYPE_LATEST, generate_latest

TOKEN = Path("/run/secrets/control_token").read_text().strip()
log = logging.getLogger("uvicorn.error")
blocked = Counter("gateway_blocked_requests_total", "Rejected requests", ["reason"])
active = Gauge("gateway_protection_active", "Temporary per-source limiter enabled")
until = 0.0
buckets = OrderedDict()
BUCKET_CAPACITY = 3.0
RATE = 1.0
MAX_CLIENTS = 4096
FORBIDDEN_HEADERS = {"host", "connection", "content-length", "transfer-encoding", "keep-alive", "upgrade", "te", "trailer", "proxy-authorization", "proxy-authenticate"}


def emit(event, **fields):
    log.info(json.dumps({"event": event, **fields}))


def admitted(source, now):
    if now >= until:
        buckets.clear()
        return True
    tokens, previous = buckets.pop(source, (BUCKET_CAPACITY, now))
    tokens = min(BUCKET_CAPACITY, tokens + max(0, now - previous) * RATE)
    allowed = tokens >= 1
    buckets[source] = (tokens - 1 if allowed else tokens, now)
    if len(buckets) > MAX_CLIENTS:
        buckets.popitem(last=False)
    return allowed


@asynccontextmanager
async def lifespan(app):
    app.state.client = httpx.AsyncClient(timeout=5, limits=httpx.Limits(max_connections=32), follow_redirects=False)
    yield
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])


def authorize(request):
    if not hmac.compare_digest(request.headers.get("x-control-token", ""), TOKEN):
        raise HTTPException(403, "Forbidden")


class Protection(BaseModel):
    seconds: int = Field(default=120, ge=15, le=120)


@app.post("/internal/protection")
async def protect(data: Protection, request: Request):
    global until
    authorize(request)
    until = time.monotonic() + data.seconds
    buckets.clear()
    emit("protection_enabled", duration_seconds=data.seconds, requests_per_second=RATE)
    return {"active": True, "duration_seconds": data.seconds}


@app.get("/internal/metrics")
def metrics():
    active.set(int(time.monotonic() < until))
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


@app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "OPTIONS", "PUT", "DELETE"])
async def proxy(path: str, request: Request):
    # The official WAF template overwrites X-Real-IP with its TCP peer address.
    # Public access to internal endpoints is blocked at the WAF as well.
    if not (path == "health" or path.startswith(("auth/", "payments", "admin/"))):
        raise HTTPException(404, "Not found")
    source = request.headers.get("x-real-ip", "")
    try:
        ipaddress.ip_address(source)
    except ValueError:
        raise HTTPException(403, "Missing trusted proxy source")
    if not admitted(source, time.monotonic()):
        blocked.labels("rate_limit").inc()
        emit("request_limited", source=source, method=request.method)
        return Response('{"detail":"Temporary rate limit"}', status_code=429,
                        headers={"Retry-After": "1", "Content-Type": "application/json"})
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > 65536:
            blocked.labels("body_size").inc()
            raise HTTPException(413, "Request too large")
    backend = "auth-api:8001" if path.startswith(("auth/", "admin/users")) else "payment-api:8000"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in FORBIDDEN_HEADERS | {"x-control-token", "x-real-ip", "x-forwarded-for"}}
    try:
        response = await request.app.state.client.request(request.method, f"http://{backend}/{path}",
            params=request.query_params.multi_items(), content=bytes(payload), headers=headers)
        return Response(response.content, status_code=response.status_code,
                        headers={k: v for k, v in response.headers.items() if k.lower() not in FORBIDDEN_HEADERS | {"content-encoding"}})
    except httpx.HTTPError:
        emit("upstream_unavailable", backend=backend)
        raise HTTPException(503, "Upstream unavailable")

"""Bounded polling controller: no Docker socket, shell or arbitrary action URL."""
import asyncio
import json
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Response
from prometheus_client import Counter, Gauge, CONTENT_TYPE_LATEST, generate_latest

log = logging.getLogger("uvicorn.error")
TOKEN = Path("/run/secrets/control_token").read_text().strip()
PROBE = json.loads(Path("/run/secrets/probe_credentials").read_text())
MIN_RPS = float(os.getenv("REMEDIATION_MIN_RPS", "1.0"))
actions = Counter("remediation_actions_total", "Bounded protection actions", ["result"])
verified = Gauge("remediation_payment_verified", "Last post-action payment and readback succeeded")
state = {"state": "waiting_for_ml"}
last_action = {}


class Policy:
    def __init__(self):
        self.last = 0
        self.streak = 0
        self.cooldown = 0

    def decide(self, sample, now):
        timestamp = sample.get("timestamp", 0)
        if (sample.get("service") != "payment-api" or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp) or not 0 <= now - timestamp <= 60
                or sample.get("state") not in ("normal", "anomaly")):
            self.streak = 0
            return False
        if timestamp <= self.last:
            return False
        if self.last and timestamp - self.last > 35:
            self.streak = 0
        self.last = timestamp
        # A rate limit is appropriate only for elevated traffic, not an idle API.
        rps = sample.get("features", {}).get("requests_per_second", 0)
        elevated = isinstance(rps, (int, float)) and math.isfinite(rps) and rps >= MIN_RPS
        self.streak = self.streak + 1 if sample.get("anomaly") is True and sample["state"] == "anomaly" and elevated else 0
        if self.streak >= 3 and now >= self.cooldown:
            self.cooldown = now + 180
            self.streak = 0
            return True
        return False


def emit(event, **fields):
    log.info(json.dumps({"event": event, **fields}))


async def verify_payment(client):
    # One dedicated fictitious account, stable across restarts via a Compose secret.
    registration = await client.post("http://auth-api:8001/auth/register", json=PROBE)
    if registration.status_code not in (201, 409):
        registration.raise_for_status()
    login = await client.post("http://waf:8080/auth/login", json=PROBE)
    login.raise_for_status()
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    payment = await client.post("http://waf:8080/payments", headers=headers, json={"amount": "1.00", "currency": "EUR"})
    payment.raise_for_status()
    record = payment.json()
    if record.get("status") != "SUCCESS":
        raise ValueError("Payment declined")
    await asyncio.sleep(1.1)
    readback = await client.get("http://waf:8080/payments/" + record["id"], headers=headers)
    readback.raise_for_status()
    if readback.json().get("id") != record["id"]:
        raise ValueError("Payment readback mismatch")
    return record["id"]


async def run():
    global state, last_action
    policy = Policy()
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
        while True:
            try:
                response = await client.get("http://ml-api:8002/status")
                response.raise_for_status()
                sample = response.json()
                if policy.decide(sample, time.time()):
                    verified.set(0)
                    response = await client.post("http://gateway:8080/internal/protection",
                        headers={"X-Control-Token": TOKEN}, json={"seconds": 120})
                    response.raise_for_status()
                    actions.labels("activated").inc()
                    emit("remediation_activated", ml_timestamp=sample["timestamp"])
                    last_action = {"activated_at": time.time(), "expires_at": time.time() + 120, "payment_verified": False}
                    state = {"state": "protection_active", "timestamp": time.time()}
                    await asyncio.sleep(2)
                    try:
                        payment_id = await verify_payment(client)
                        verified.set(1)
                        actions.labels("payment_verified").inc()
                        emit("payment_verified", payment_id=payment_id)
                        last_action.update(payment_verified=True, payment_id=payment_id, verified_at=time.time())
                        state = {"state": "payment_verified", "payment_id": payment_id, "timestamp": time.time()}
                    except Exception as exc:
                        actions.labels("verification_failed").inc()
                        emit("verification_failed", error_type=type(exc).__name__)
                        state = {"state": "verification_failed", "timestamp": time.time()}
                elif sample.get("state") not in ("normal", "anomaly"):
                    state = {"state": "waiting_for_valid_ml"}
                else:
                    state = {"state": "monitoring", "ml_state": sample["state"], "timestamp": time.time()}
            except Exception as exc:
                policy.streak = 0
                emit("controller_unavailable", error_type=type(exc).__name__)
                state = {"state": "dependency_unavailable"}
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/status")
def status():
    return {**state, "last_action": last_action}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})

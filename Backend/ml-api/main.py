"""Explicit baseline training; missing telemetry is never treated as normal."""
import asyncio
import logging
import math
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import Gauge, CONTENT_TYPE_LATEST, generate_latest
from sklearn.ensemble import IsolationForest

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
JOB = os.getenv("TARGET_JOB", "payment-api")
if not JOB or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in JOB):
    raise ValueError("TARGET_JOB must be a simple Prometheus job name")
INTERVAL = 15
MIN_SAMPLES = 60
QUERIES = {
    "requests_per_second": f'sum(rate(http_requests_total{{job="{JOB}"}}[1m]))',
    "errors_per_second": f'sum(rate(http_requests_total{{job="{JOB}",status=~"5.."}}[1m]))',
    "mean_latency_seconds": f'sum(rate(http_request_duration_seconds_sum{{job="{JOB}"}}[1m])) / clamp_min(sum(rate(http_request_duration_seconds_count{{job="{JOB}"}}[1m])), 0.000001)',
}
ready = Gauge("ml_model_ready", "Baseline model trained", ["service"])
available = Gauge("ml_telemetry_available", "Last collection succeeded", ["service"])
flag = Gauge("ml_anomaly_detected", "Anomaly: 1, normal: 0; valid only with telemetry available", ["service"])
score = Gauge("ml_anomaly_score", "Negative decision function; positive means anomalous", ["service"])
last_success = Gauge("ml_last_analysis_timestamp_seconds", "Last successful analysis", ["service"])
log = logging.getLogger("uvicorn.error")
model = None
latest = {"state": "untrained", "service": JOB}
training = False


async def series(client, query, start, end):
    response = await client.get(f"{PROMETHEUS_URL}/api/v1/query_range", params={
        "query": query, "start": start, "end": end, "step": INTERVAL,
    })
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success" or payload.get("data", {}).get("resultType") != "matrix":
        raise ValueError("Invalid Prometheus response")
    results = payload["data"]["result"]
    if len(results) != 1:
        raise ValueError("Expected one time series; check instrumentation and job")
    return {float(t): float(v) for t, v in results[0]["values"] if math.isfinite(float(v))}


async def observations(start, end):
    async with httpx.AsyncClient(timeout=10) as client:
        columns = await asyncio.gather(*(series(client, q, start, end) for q in QUERIES.values()))
        up = await series(client, f'min(up{{job="{JOB}"}})', start, end)
    times = sorted(set.intersection(*(set(c) for c in columns), set(up)))
    return [(t, [c[t] for c in columns]) for t in times if up[t] == 1]


async def analyze():
    global latest
    while True:
        if model is not None:
            try:
                end = int(time.time()) - INTERVAL
                rows = await observations(end - 2 * INTERVAL, end)
                if not rows or rows[-1][0] < end - INTERVAL:
                    raise ValueError("Missing or stale telemetry; target may be down")
                timestamp, values = rows[-1]
                current_model = model
                decision = float((await asyncio.to_thread(current_model.decision_function, [values]))[0])
                anomaly = decision < 0
                latest = {"state": "anomaly" if anomaly else "normal", "service": JOB,
                          "timestamp": timestamp, "anomaly": anomaly, "score": -decision,
                          "features": dict(zip(QUERIES, values))}
                available.labels(JOB).set(1)
                flag.labels(JOB).set(int(anomaly))
                score.labels(JOB).set(-decision)
                last_success.labels(JOB).set(time.time())
                if anomaly:
                    log.warning("ML anomaly: %s", latest)
            except Exception as exc:
                available.labels(JOB).set(0)
                flag.labels(JOB).set(float("nan"))
                score.labels(JOB).set(float("nan"))
                latest = {"state": "telemetry_unavailable", "service": JOB}
                log.warning("ML collection failed: %s", exc)
        await asyncio.sleep(INTERVAL)


@asynccontextmanager
async def lifespan(app):
    ready.labels(JOB).set(0)
    available.labels(JOB).set(0)
    task = asyncio.create_task(analyze())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="RESILIENCE-ALPHA anomaly detection", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy", "model_ready": model is not None}


@app.get("/status")
def status():
    return latest


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


@app.post("/train")
async def train():
    """Operator explicitly certifies the preceding 30 minutes as baseline."""
    global model, training, latest
    if training:
        raise HTTPException(409, "Training already in progress")
    training = True
    try:
        end = int(time.time()) - INTERVAL
        rows = await observations(end - 1800, end)
        if len(rows) < MIN_SAMPLES:
            raise HTTPException(409, "Need at least 60 complete healthy observations, 15s apart")
        candidate = IsolationForest(n_estimators=100, contamination="auto", random_state=42, n_jobs=1)
        await asyncio.to_thread(candidate.fit, [values for _, values in rows])
        model = candidate
        ready.labels(JOB).set(1)
        latest = {"state": "trained_waiting_for_analysis", "service": JOB}
        return {"samples": len(rows), "start": rows[0][0], "end": rows[-1][0], "features": list(QUERIES)}
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Training failed: %s", exc)
        raise HTTPException(503, "Prometheus telemetry unavailable; check collector configuration") from exc
    finally:
        training = False

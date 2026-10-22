"""Explicit baseline training; missing telemetry is never treated as normal."""
import asyncio
import logging
import math
import os
import time
import json
import uuid
from pathlib import Path
from typing import Annotated
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Response, Query
from prometheus_client import Gauge, CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from detector import BaselineDetector, MODEL_VERSION, FEATURES

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
model_metadata = {}
STATE_PATH = os.getenv('MODEL_STATE_PATH')


async def current_profile():
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.get('http://payment-api:8000/lab-profile')
        response.raise_for_status()
        profile = response.json()
    return {key: profile[key] for key in ('enabled', 'slots', 'simulated_work_seconds')}


def baseline_state(rows, profile, provenance):
    metadata = {'model_id': uuid.uuid4().hex, 'job': JOB, 'profile': profile,
                'version': MODEL_VERSION, 'features': FEATURES, 'provenance': provenance,
                'samples': len(rows), 'start': rows[0][0], 'end': rows[-1][0],
                'created_at': time.time()}
    return {'metadata': metadata, 'observations': rows}


def persist(state):
    if not STATE_PATH:
        return
    target = Path(STATE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, allow_nan=False), encoding='utf-8')
    temporary.replace(target)


def publish(candidate, state):
    global model, model_metadata, latest
    # Persist first: a storage failure must not silently publish an unpersisted model.
    persist(state)
    model = candidate
    model_metadata = state['metadata']
    ready.labels(JOB).set(1)
    latest = {'state': 'trained_waiting_for_analysis', 'service': JOB,
              'model_id': model_metadata['model_id']}


async def restore():
    if not STATE_PATH or not Path(STATE_PATH).is_file():
        return
    try:
        saved = json.loads(Path(STATE_PATH).read_text(encoding='utf-8'))
        metadata = saved['metadata']
        if (metadata['version'] != MODEL_VERSION or metadata['job'] != JOB
                or metadata['features'] != FEATURES or metadata['profile'] != await current_profile()):
            raise ValueError('Saved baseline does not match current profile/model')
        rows = saved['observations']
        if len(rows) < MIN_SAMPLES:
            raise ValueError('Incomplete saved baseline')
        candidate = await asyncio.to_thread(BaselineDetector().fit, [row[1] for row in rows])
        publish(candidate, saved)
        log.info('Restored certified baseline model_id=%s', metadata['model_id'])
    except Exception as exc:
        log.warning('Saved baseline not restored: %s', type(exc).__name__)


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
        if model is None:
            # Retry restoration if dependencies were not ready at container startup.
            # This reuses the SAME saved baseline; never learns incident traffic.
            await restore()
        if model is not None:
            try:
                if model_metadata and model_metadata['profile'] != await current_profile():
                    ready.labels(JOB).set(0)
                    available.labels(JOB).set(0)
                    flag.labels(JOB).set(float('nan'))
                    score.labels(JOB).set(float('nan'))
                    latest = {'state': 'profile_mismatch', 'service': JOB}
                    await asyncio.sleep(INTERVAL)
                    continue
                end = int(time.time()) - INTERVAL
                rows = await observations(end - 2 * INTERVAL, end)
                if not rows or rows[-1][0] < end - INTERVAL:
                    raise ValueError("Missing or stale telemetry; target may be down")
                timestamp, values = rows[-1]
                current_model = model
                decision = float((await asyncio.to_thread(current_model.decision_function, [values]))[0])
                anomaly = decision < 0
                latest = {"state": "anomaly" if anomaly else "normal", "service": JOB,
                          "model_id": model_metadata.get('model_id'),
                          "timestamp": timestamp, "anomaly": anomaly, "score": -decision,
                          "features": dict(zip(QUERIES, values))}
                available.labels(JOB).set(1)
                ready.labels(JOB).set(1)
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
    await restore()
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
    return {"status": "healthy", "model_ready": model is not None and latest.get('state') != 'profile_mismatch',
            'model_id': model_metadata.get('model_id'), 'model': model_metadata}


@app.get("/status")
def status():
    return latest


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


@app.post("/train")
async def train(minutes: Annotated[int, Query(ge=16, le=120)] = 30):
    """Operator explicitly certifies the selected recent window as baseline."""
    global model, training, latest
    if training:
        raise HTTPException(409, "Training already in progress")
    training = True
    try:
        end = int(time.time()) - INTERVAL
        rows = await observations(end - minutes * 60, end)
        if len(rows) < MIN_SAMPLES:
            raise HTTPException(409, "Need at least 60 complete healthy observations, 15s apart")
        profile = await current_profile()
        candidate = BaselineDetector()
        await asyncio.to_thread(candidate.fit, [values for _, values in rows])
        state = baseline_state(rows, profile, 'operator-selected recent normal window')
        publish(candidate, state)
        return {**state['metadata'], 'detector': candidate.description()}
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Training failed: %s", exc)
        raise HTTPException(503, "Prometheus telemetry unavailable; check collector configuration") from exc
    finally:
        training = False


class HistoricalWindow(BaseModel):
    start: float
    end: float
    samples: int
    validation_start: float
    validation_end: float


@app.post('/replay-baseline')
async def replay_baseline(window: HistoricalWindow):
    """Reuse an operator-selected existing reference; validation never enters fit()."""
    global training
    if training:
        raise HTTPException(409, 'Training already in progress')
    bounds = [window.start, window.end, window.validation_start, window.validation_end]
    if (not all(math.isfinite(value) for value in bounds)
            or not 0 < window.start < window.end < window.validation_start <= window.validation_end < time.time()
            or not 900 <= window.end - window.start <= 7200
            or window.validation_end - window.validation_start > 3600):
        raise HTTPException(422, 'Invalid or overlapping historical windows')
    training = True
    try:
        profile = await current_profile()
        if profile.get('enabled') is not True:
            raise HTTPException(409, 'Historical DoS reference requires the reduced-capacity profile')
        rows = await observations(window.start, window.end)
        if len(rows) < MIN_SAMPLES or len(rows) != window.samples:
            raise HTTPException(409, 'Historical reference incomplete; no fabricated observations used')
        validation = await observations(window.validation_start, window.validation_end)
        if len(validation) < 5:
            raise HTTPException(409, 'Historical validation telemetry missing')
        candidate = await asyncio.to_thread(BaselineDetector().fit, [values for _, values in rows])
        decisions = await asyncio.to_thread(candidate.decision_function, [values for _, values in validation])
        fraction = float(sum(value < 0 for value in decisions) / len(decisions))
        result = {'false_alert_fraction': fraction, 'samples': len(validation),
                  'scores': [-float(value) for value in decisions]}
        if fraction > 0.2:
            raise HTTPException(409, {'message': 'Historical validation still fails; model not published', **result})
        state = baseline_state(rows, profile, 'replayed existing certified normal window; fresh live validation required')
        publish(candidate, state)
        return {'training': {**state['metadata'], 'detector': candidate.description()},
                'historical_validation': result, 'requires_fresh_validation': True}
    except HTTPException:
        raise
    except Exception as exc:
        log.warning('Historical replay failed: %s', type(exc).__name__)
        raise HTTPException(503, 'Historical telemetry or model storage unavailable') from exc
    finally:
        training = False

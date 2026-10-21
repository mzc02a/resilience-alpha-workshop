"""One-shot normal baseline in the explicit lab profile, then held-out validation."""
import asyncio
import json
import random
import time
from pathlib import Path

import httpx
from remediator import PROBE


async def main():
    report_path = Path("/reports/dos-calibration.json")
    report = {"state": "collecting", "started_at": time.time(), "requests": 0}
    def save():
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    async with httpx.AsyncClient(timeout=10) as client:
        profile = (await client.get("http://payment-api:8000/lab-profile")).json()
        if not profile.get("enabled"):
            raise RuntimeError("Lab profile must be enabled")
        signup = await client.post("http://auth-api:8001/auth/register", json=PROBE)
        if signup.status_code not in (201, 409):
            signup.raise_for_status()
        login = await client.post("http://waf:8080/auth/login", json=PROBE)
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        save()
        deadline = time.monotonic() + 17 * 60
        try:
            while time.monotonic() < deadline:
                result = await client.get("http://waf:8080/payments", headers=headers)
                result.raise_for_status()
                report["requests"] += 1
                save()
                await asyncio.sleep(random.uniform(0.7, 1.3))
            result = await client.post("http://ml-api:8002/train", params={"minutes": 16}, timeout=30)
            result.raise_for_status()
            report.update(state="validating", training=result.json(), validation=[])
            save()
            deadline = time.monotonic() + 180
            next_sample = time.monotonic() + 60
            while time.monotonic() < deadline:
                result = await client.get("http://waf:8080/payments", headers=headers)
                result.raise_for_status()
                await asyncio.sleep(random.uniform(0.7, 1.3))
                if time.monotonic() >= next_sample:
                    report["validation"].append((await client.get("http://ml-api:8002/status")).json())
                    save()
                    next_sample = time.monotonic() + 15
            states = [sample.get("state") for sample in report["validation"]]
            valid = len(states) >= 5 and all(state in ("normal", "anomaly") for state in states)
            rate = states.count("anomaly") / len(states) if states else None
            report.update(state="ready" if valid and rate <= 0.2 else "review_required", false_alert_fraction=rate,
                          finished_at=time.time())
        except Exception as exc:
            report.update(state="failed", error_type=type(exc).__name__, finished_at=time.time())
            raise
        finally:
            save()
            print(json.dumps({key: value for key, value in report.items() if key != "validation"}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

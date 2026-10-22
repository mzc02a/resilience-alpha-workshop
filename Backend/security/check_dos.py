"""Component acceptance test: explicit manual activation, not a claim of ML detection."""
import asyncio
import json
import time
from collections import Counter

import httpx
from remediator import PROBE, TOKEN


async def main():
    async with httpx.AsyncClient(timeout=5) as client:
        async def legitimate_probe():
            result = await client.post("http://remediator:8081/internal/probe", headers={"X-Control-Token": TOKEN}, timeout=15)
            result.raise_for_status()
            return result.json()["payment_id"]
        profile = (await client.get("http://payment-api:8000/lab-profile")).json()
        assert profile["enabled"], "Enable the explicit DoS lab profile"
        baseline = await legitimate_probe()
        login = await client.post("http://waf:8080/auth/login", json=PROBE)
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        stop = asyncio.Event()
        statuses = Counter()
        async def load():
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not stop.is_set():
                tick = time.monotonic()
                response = await client.get("http://waf:8080/payments", headers=headers)
                statuses[response.status_code] += 1
                await asyncio.sleep(max(0, 1 - (time.monotonic() - tick)))
        tasks = [asyncio.create_task(load()) for _ in range(4)]
        before = []
        recovered = []
        try:
            await asyncio.sleep(2)
            for _ in range(5):
                try:
                    await legitimate_probe()
                    before.append(True)
                except httpx.HTTPStatusError:
                    before.append(False)
                await asyncio.sleep(0.5)
            assert False in before, "No business degradation observed; do not claim DoS"
            control = await client.post("http://gateway:8080/internal/protection",
                headers={"X-Control-Token": TOKEN}, json={"seconds": 30})
            control.raise_for_status()
            await asyncio.sleep(3)
            for _ in range(3):
                recovered.append(await legitimate_probe())
                await asyncio.sleep(1)
            assert statuses[429] > 0
            assert not all(task.done() for task in tasks), "Load must still be active during recovery"
        finally:
            stop.set()
            await asyncio.gather(*tasks)
        report = {"kind": "component test; limiter explicitly activated by test", "profile": profile,
                  "baseline_payment": baseline, "payments_success_before_protection": before,
                  "payments_verified_during_continued_load": recovered, "load_statuses": dict(statuses)}
        print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main())

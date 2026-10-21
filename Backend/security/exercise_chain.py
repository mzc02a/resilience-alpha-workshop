"""100s bounded end-to-end experiment against this Compose project only."""
import asyncio
import json
import time
from collections import Counter

import httpx
from remediator import PROBE


async def main():
    async with httpx.AsyncClient(timeout=5) as client:
        ml = await client.get("http://ml-api:8002/health")
        assert ml.json().get("model_ready"), "Train ML on normal traffic first"
        login = await client.post("http://waf:8080/auth/login", json=PROBE)
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        started = time.time()
        statuses = Counter()
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            tick = time.monotonic()
            response = await client.get("http://waf:8080/payments", headers=headers)
            statuses[response.status_code] += 1
            await asyncio.sleep(max(0, 1 / 3 - (time.monotonic() - tick)))
        result = (await client.get("http://remediator:8081/status")).json()
        last_action = result.get("last_action", {})
        success = (last_action.get("payment_verified") is True and last_action.get("verified_at", 0) >= started and statuses[429] > 0)
        print(json.dumps({"experiment": "3 requests/s for 100s", "statuses": dict(statuses),
                          "controller": result, "chain_verified": success}))
        if not success:
            raise SystemExit("Chain not verified: inspect model and controller; do not claim recovery")


if __name__ == "__main__":
    asyncio.run(main())

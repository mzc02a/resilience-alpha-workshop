"""Run inside the remediator image: verifies the actual WAF and one fictitious payment."""
import asyncio
import json
from pathlib import Path

import httpx
from remediator import TOKEN, verify_payment


async def main():
    async with httpx.AsyncClient(timeout=10) as client:
        health = await client.get("http://waf:8080/health")
        assert health.status_code == 200, health.status_code
        internal = await client.get("http://waf:8080/internal/metrics")
        assert internal.status_code == 404, internal.status_code
        attack = await client.get("http://waf:8080/payments", params={"q": "<script>alert(1)</script>"})
        assert attack.status_code == 403, attack.status_code
        control = await client.post("http://gateway:8080/internal/protection", json={"seconds": 15})
        assert control.status_code == 403, control.status_code
        control = await client.post("http://gateway:8080/internal/protection", headers={"X-Control-Token": TOKEN}, json={"seconds": 15})
        control.raise_for_status()
        statuses = []
        for i in range(8):
            result = await client.get("http://waf:8080/payments", headers={"X-Real-IP": f"192.0.2.{i+1}"})
            statuses.append(result.status_code)
        assert 429 in statuses, statuses
        await asyncio.sleep(16)
        payment_id = await verify_payment(client)
        print(json.dumps({"waf_health": 200, "public_management_blocked": 404,
            "xss_blocked": 403, "unauthorized_control_blocked": 403,
            "limiter_statuses": statuses, "limiter_expired": True,
            "payment_verified": payment_id}))


if __name__ == "__main__":
    asyncio.run(main())

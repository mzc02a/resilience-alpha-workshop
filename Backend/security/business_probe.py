"""Continuous real fictitious payments, without retrying ambiguous POST requests."""
import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from lab_session import load_session, auth_headers, preflight, transfer, readback_matches


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, choices=range(30, 601), default=240, metavar="30..600")
    parser.add_argument("--output", default="/reports/business-probe.json")
    args = parser.parse_args()
    records = []
    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
        session = load_session(minimum=args.seconds + 60)
        await preflight(client, session)
        headers = auth_headers(session)
        deadline = time.monotonic() + args.seconds
        try:
            while time.monotonic() < deadline:
                started = time.monotonic()
                row = {"timestamp": time.time(), "success": False}
                try:
                    payment = await transfer(client, session)
                    row["post_status"] = payment.status_code
                    if payment.status_code == 201:
                        data = payment.json()
                        row["payment_id"] = data["id"]
                        # Separate the two requests to avoid triggering a legitimate-source burst.
                        await asyncio.sleep(1.1)
                        readback = await client.get("http://waf:8080/payments/" + data["id"], headers=headers)
                        row["read_status"] = readback.status_code
                        row["success"] = (readback.status_code == 200 and readback_matches(data, readback.json()))
                except httpx.HTTPError as exc:
                    row["error_type"] = type(exc).__name__
                row["duration_seconds"] = time.monotonic() - started
                records.append(row)
                Path(args.output).write_text(json.dumps(records, indent=2), encoding="utf-8")
                print(json.dumps(row), flush=True)
                await asyncio.sleep(max(0, 4 - (time.monotonic() - started)))
        finally:
            Path(args.output).write_text(json.dumps(records, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

"""Small bounded traffic experiment, restricted to the team's Windows hotspot."""
import argparse
import csv
import getpass
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward credentials or JWT to another destination.
        return None


def lab_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def validate_url(value):
    target = urllib.parse.urlsplit(value)
    try:
        address = ipaddress.ip_address(target.hostname)
        valid = address.is_loopback or address in ipaddress.ip_network("192.168.137.0/24")
    except ValueError:
        valid = target.hostname == "localhost"
    if (not valid or target.scheme != "http" or target.username or target.password
            or target.path not in ("", "/") or target.query or target.fragment
            or target.port != 8080):
        raise ValueError("Cible autorisee : HTTP, port 8080, localhost ou hotspot 192.168.137.0/24, sans chemin.")
    return urllib.parse.urlunsplit((target.scheme, target.netloc, "", "", ""))


def summary(rows):
    def p95(values):
        values = sorted(values)
        return values[max(0, (95 * len(values) + 99) // 100 - 1)] if values else None
    return {"requests": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
            "latency_all_p95_ms": p95([r["duration_ms"] for r in rows]),
            "latency_200_p95_ms": p95([r["duration_ms"] for r in rows if r["status"] == "200"])}


def main():
    parser = argparse.ArgumentParser(description="Authorized hotspot-only test; maximum 5 req/s for 180s")
    parser.add_argument("--url", default="http://192.168.137.1:8080")
    parser.add_argument("--seconds", type=int, choices=range(15, 181), default=120, metavar="15..180")
    parser.add_argument("--rps", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--ramp", action="store_true", help="20s a 1 req/s puis --rps ; inclus dans --seconds")
    args = parser.parse_args()
    try:
        args.url = validate_url(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    if args.ramp and args.seconds <= 20:
        parser.error("--ramp necessite plus de 20 secondes")
    email = input("Compte fictif autorisé : ")
    password = getpass.getpass("Mot de passe (masqué) : ")
    login = urllib.request.Request(args.url.rstrip("/") + "/auth/login", headers={"Content-Type": "application/json"},
        data=json.dumps({"email": email, "password": password}).encode())
    try:
        with lab_opener().open(login, timeout=5) as response:
            token = json.load(response)["access_token"]
        if not isinstance(token, str) or not token:
            raise ValueError("Missing token")
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        raise SystemExit("Connexion refusée ou indisponible ; aucun trafic de test lancé.")
    del password
    folder = Path(__file__).resolve().parent / "results"
    folder.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime("traffic-%Y%m%dT%H%M%S%fZ")
    csv_path = folder / (stem + ".csv")
    output = csv_path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(output, fieldnames=["started_at_utc", "phase", "requested_rps", "status", "duration_ms", "error"])
    writer.writeheader()
    rows = []
    started_at = datetime.now(timezone.utc).isoformat()
    begin = time.monotonic()
    deadline = begin + args.seconds
    interrupted = False
    previous_phase = None
    print("Trafic demarre : prevenir le PC hote. Arret immediat des nouveaux envois : Ctrl+C.", flush=True)
    try:
        while time.monotonic() < deadline:
            started = time.monotonic()
            phase = "reference" if args.ramp and started - begin < 20 else "charge"
            rps = 1 if phase == "reference" else args.rps
            stamp = datetime.now(timezone.utc).isoformat()
            if phase != previous_phase:
                print(f"{stamp} phase={phase}, plafond={rps} req/s", flush=True)
                previous_phase = phase
            status, error = "unavailable", ""
            req = urllib.request.Request(args.url.rstrip("/") + "/payments", headers={"Authorization": "Bearer " + token})
            try:
                with lab_opener().open(req, timeout=3) as response:
                    while response.read(65536):
                        if time.monotonic() - started > 4:
                            raise TimeoutError()
                    status = str(response.status)
            except urllib.error.HTTPError as exc:
                status = str(exc.code)
                exc.close()
            except (urllib.error.URLError, OSError) as exc:
                error = type(exc).__name__
            row = {"started_at_utc": stamp, "phase": phase, "requested_rps": rps,
                   "status": status, "duration_ms": round((time.monotonic() - started) * 1000, 2), "error": error}
            rows.append(row)
            writer.writerow(row)
            output.flush()
            time.sleep(max(0, min(1 / rps - (time.monotonic() - started), deadline - time.monotonic())))
    except KeyboardInterrupt:
        interrupted = True
        print("Arret demande.")
    finally:
        output.close()
        token = None
    elapsed = time.monotonic() - begin
    report = {"target": args.url, "started_at_utc": started_at,
              "finished_at_utc": datetime.now(timezone.utc).isoformat(),
              "requested_rps": args.rps, "planned_seconds": args.seconds,
              "actual_seconds": round(elapsed, 2), "actual_rps": round(len(rows) / elapsed, 3) if elapsed else 0,
              "interrupted": interrupted, **summary(rows),
              "phases": {phase: summary([r for r in rows if r["phase"] == phase])
                         for phase in dict.fromkeys(r["phase"] for r in rows)},
              "interpretation": "429 = limitation ; DoS et reprise necessitent une mesure metier independante."}
    json_path = folder / (stem + ".json")
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Rapports sans identifiants ni JWT :\n{csv_path}\n{json_path}")


if __name__ == "__main__":
    main()

"""Small bounded traffic experiment, restricted to the team's Windows hotspot."""
import argparse
import getpass
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from bounded_load import run_load
from lab_session import token_lifetime


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description="Authorized hotspot-only test; maximum 5 req/s for 180s")
    parser.add_argument("--url", default="http://192.168.137.1:8080")
    parser.add_argument("--seconds", type=int, choices=range(15, 181), default=120, metavar="15..180")
    parser.add_argument("--rps", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--output", default="dos-traffic.json")
    args = parser.parse_args()
    target = urllib.parse.urlparse(args.url)
    try:
        address = ipaddress.ip_address(target.hostname)
        valid = address.is_loopback or address in ipaddress.ip_network("192.168.137.0/24")
    except ValueError:
        valid = target.hostname in ("localhost", "127.0.0.1")
    if (not valid or target.scheme != "http" or target.username or target.password
            or target.path not in ("", "/") or target.query or target.fragment or target.port != 8080):
        parser.error("Only localhost or the authorized 192.168.137.0/24 hotspot is supported")
    token = getpass.getpass("JWT du compte fictif apres connexion faciale complete (saisie masquee) : ")
    try:
        token_lifetime(token, args.seconds + 60)
        req = urllib.request.Request(args.url.rstrip("/") + "/auth/me", headers={"Authorization": "Bearer " + token})
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with direct.open(req, timeout=5) as response:
            json.load(response)
    except (urllib.error.URLError, ValueError, OSError):
        raise SystemExit("Session refusee, non active ou trop courte ; aucun trafic lance. Refaire la connexion complete.")
    records = run_load(args.url, token, args.seconds, args.rps)
    report = {"requested_rps": args.rps, "duration_seconds": args.seconds,
              "statuses": dict(Counter(str(row["status"]) for row in records)), "requests": records}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"statuses": report["statuses"], "report": args.output}))


if __name__ == "__main__":
    main()

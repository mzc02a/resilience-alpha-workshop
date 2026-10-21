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


def main():
    parser = argparse.ArgumentParser(description="Authorized hotspot-only test; maximum 5 req/s for 180s")
    parser.add_argument("--url", default="http://192.168.137.1:8080")
    parser.add_argument("--seconds", type=int, choices=range(15, 181), default=120, metavar="15..180")
    parser.add_argument("--rps", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    target = urllib.parse.urlparse(args.url)
    try:
        address = ipaddress.ip_address(target.hostname)
        valid = address.is_loopback or address in ipaddress.ip_network("192.168.137.0/24")
    except ValueError:
        valid = target.hostname in ("localhost", "127.0.0.1")
    if not valid or target.scheme != "http" or target.username or target.password or target.path not in ("", "/"):
        parser.error("Only localhost or the authorized 192.168.137.0/24 hotspot is supported")
    email = input("Compte fictif autorisé : ")
    password = getpass.getpass("Mot de passe (masqué) : ")
    login = urllib.request.Request(args.url.rstrip("/") + "/auth/login", headers={"Content-Type": "application/json"},
        data=json.dumps({"email": email, "password": password}).encode())
    try:
        with urllib.request.urlopen(login, timeout=5) as response:
            token = json.load(response)["access_token"]
    except urllib.error.URLError:
        raise SystemExit("Connexion refusée ou indisponible ; aucun trafic de test lancé.")
    del password
    results = Counter()
    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            started = time.monotonic()
            req = urllib.request.Request(args.url.rstrip("/") + "/payments", headers={"Authorization": "Bearer " + token})
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    response.read()
                    results[response.status] += 1
            except urllib.error.HTTPError as exc:
                results[exc.code] += 1
            except (urllib.error.URLError, TimeoutError):
                results["unavailable"] += 1
            time.sleep(max(0, 1 / args.rps - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    print(json.dumps({"requested_rps": args.rps, "statuses": dict(results)}))


if __name__ == "__main__":
    main()

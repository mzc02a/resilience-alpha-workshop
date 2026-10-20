"""Low-rate baseline traffic against an explicitly selected fictitious lab API."""
import argparse
import getpass
import json
import random
import time
import urllib.error
import urllib.request


def request(url, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description="Normal fictitious traffic; writes simulated payments.")
    parser.add_argument("--auth-url", default="http://localhost:8001")
    parser.add_argument("--payment-url", default="http://localhost:8000")
    parser.add_argument("--minutes", type=int, default=30, choices=range(1, 61), metavar="1..60")
    args = parser.parse_args()
    email = input("E-mail du compte de test fictif : ")
    password = getpass.getpass("Mot de passe (masqué) : ")
    token = request(args.auth_url.rstrip("/") + "/auth/login", body={"email": email, "password": password})["access_token"]
    del password
    deadline = time.monotonic() + args.minutes * 60
    completed = 0
    token_time = time.monotonic()
    print("Trafic normal : environ 1 requête toutes les 1 à 3 secondes ; un paiement toutes les 10 requêtes.")
    try:
        while time.monotonic() < deadline:
            # Tokens expire after 30 minutes. Stop before expiry rather than polluting the baseline.
            if time.monotonic() - token_time > 28 * 60:
                print("Arrêt avant expiration du JWT. Relancer pour une autre session.")
                break
            body = {"amount": round(random.uniform(1, 999), 2), "currency": "EUR"} if completed % 10 == 0 else None
            request(args.payment_url.rstrip("/") + "/payments", token, body)
            completed += 1
            if completed % 30 == 0:
                print(f"{completed} requêtes réussies")
            time.sleep(random.uniform(1, 3))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Arrêt : HTTP {exc.code}. Corriger le service avant d'entraîner le modèle.") from exc
    except urllib.error.URLError as exc:
        raise SystemExit("Arrêt : API injoignable. Ne pas entraîner sur cette période.") from exc
    except KeyboardInterrupt:
        print("Arrêt demandé.")
    print(f"Terminé : {completed} requêtes réussies. Vérifier les métriques avant /train.")


if __name__ == "__main__":
    main()

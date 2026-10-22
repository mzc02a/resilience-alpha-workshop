"""Low-rate read traffic, with optional small internal demo transfers, after facial login."""
import argparse
import base64
import getpass
import json
import random
import time
import urllib.error
import urllib.request
import uuid


def request(url, token, body=None):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    data = None if body is None else json.dumps(body).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description="Trafic normal fictif apres connexion faciale.")
    parser.add_argument("--payment-url", default="http://localhost:8000")
    parser.add_argument("--minutes", type=int, default=25, choices=range(1, 61), metavar="1..60")
    parser.add_argument("--recipient-iban", help="Active de petits virements vers ce compte de laboratoire")
    args = parser.parse_args()
    token = getpass.getpass("JWT de session du compte de test (apres visage et mot de passe, saisie masquee) : ")
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        # Only schedule around expiry; the API still verifies the signature and both factors.
        if claims.get("nbf", 0) > time.time():
            raise ValueError("Attendre la fin du delai de connexion")
        deadline = min(time.monotonic() + args.minutes * 60,
                       time.monotonic() + float(claims["exp"]) - time.time() - 30)
        if deadline <= time.monotonic():
            raise ValueError("Session expiree ou trop proche de son expiration")
    except (ValueError, KeyError, IndexError) as exc:
        raise SystemExit("JWT non utilisable pour cette session de test") from exc
    completed = 0
    print("Trafic normal : une requete toutes les 1 a 3 secondes. Aucun entrainement automatique.")
    try:
        while time.monotonic() < deadline:
            body = None
            if args.recipient_iban and completed % 10 == 0:
                body = {"recipient_iban": args.recipient_iban,
                        "amount": f"{random.randint(1, 100) / 100:.2f}", "currency": "EUR"}
            result = request(args.payment_url.rstrip("/") + "/payments", token, body)
            if body is not None and result.get("status") != "SUCCESS":
                raise SystemExit("Arret : virement refuse, verifier le solde et le destinataire")
            completed += 1
            if completed % 30 == 0:
                print(f"{completed} requetes reussies")
            time.sleep(random.uniform(1, 3))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Arret : HTTP {exc.code}. Corriger le service avant l'entrainement.") from exc
    except urllib.error.URLError as exc:
        raise SystemExit("Arret : API injoignable") from exc
    except KeyboardInterrupt:
        print("Arret demande")
    print(f"Termine : {completed} requetes. Verifier les metriques avant /train.")


if __name__ == "__main__":
    main()

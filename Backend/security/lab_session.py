"""Reuse a real facial session. APIs remain authoritative for signature/access checks."""
import base64
import json
import math
import os
import time
import uuid
from decimal import Decimal
from pathlib import Path

SESSION_PATH = os.getenv("LAB_SESSION_FILE", "/lab/probe-session.json")

class LabSessionError(ValueError):
    pass


def token_lifetime(token, minimum=0):
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        now = time.time()
        dates = [claims["exp"], claims["face_verified_until"], claims.get("nbf", 0)]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in dates):
            raise ValueError()
        if (claims.get("auth_flow") != "identifier_face_password"
                or claims.get("face_method") != "image_comparison"
                or float(claims.get("nbf", 0)) > now):
            raise ValueError()
        remaining = min(float(claims["exp"]), float(claims["face_verified_until"])) - now
        if remaining < minimum:
            raise ValueError()
        return remaining
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        raise LabSessionError("Session faciale absente, non active ou trop courte. Reconnecter le compte puis relancer prepare-lab.ps1.") from None


def load_session(minimum=60, path=None):
    try:
        session = json.loads(Path(path or SESSION_PATH).read_text(encoding="utf-8-sig"))
        token = session["access_token"]
        recipient = session["recipient_iban"]
        if not isinstance(recipient, str) or not recipient.strip():
            raise ValueError()
        token_lifetime(token, minimum)
        return session
    except (OSError, ValueError, KeyError, TypeError):
        raise LabSessionError("Session de sonde absente, invalide ou expiree. Executer cyber/prepare-lab.ps1 sur le PC hote.") from None


def auth_headers(session):
    return {"Authorization": "Bearer " + session["access_token"]}


async def preflight(client, session):
    headers = auth_headers(session)
    # No registration/login and no transfer: verify real access before a long run.
    identity = await client.get("http://waf:8080/auth/me", headers=headers)
    identity.raise_for_status()
    account = await client.get("http://waf:8080/accounts/me", headers=headers)
    account.raise_for_status()
    beneficiaries = await client.get("http://waf:8080/beneficiaries", headers=headers)
    beneficiaries.raise_for_status()
    if session["recipient_iban"] == account.json().get("iban"):
        raise LabSessionError("Choisir un autre compte destinataire que le compte de sonde.")
    if not any(b.get("iban") == session["recipient_iban"] for b in beneficiaries.json()):
        raise LabSessionError("IBAN destinataire absent des beneficiaires du laboratoire.")
    if Decimal(str(account.json().get("balance", 0))) < 1:
        raise LabSessionError("Solde de la sonde insuffisant pour un virement de 1 EUR.")


async def transfer(client, session):
    token_lifetime(session["access_token"], 5)
    return await client.post("http://waf:8080/payments",
        headers={**auth_headers(session), "Idempotency-Key": uuid.uuid4().hex},
        json={"recipient_iban": session["recipient_iban"], "amount": "1.00", "currency": "EUR"})


def readback_matches(created, readback):
    return (created.get("status") == "SUCCESS" and readback.get("status") == "SUCCESS"
            and created.get("id") == readback.get("id")
            and created.get("recipient_iban") == readback.get("recipient_iban")
            and Decimal(str(created.get("amount", 0))) == Decimal("1.00")
            and Decimal(str(readback.get("amount", 0))) == Decimal("1.00"))

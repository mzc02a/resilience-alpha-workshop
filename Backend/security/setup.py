"""Generate local-only credentials without printing them. Run before Compose."""
import json
import secrets
import uuid
from pathlib import Path

root = Path(__file__).resolve().parent / "runtime"
root.mkdir(exist_ok=True)
for name, value in {
    "control-token.txt": secrets.token_urlsafe(32),
    "probe.json": json.dumps({"email": f"probe-{uuid.uuid4().hex}@example.com", "password": secrets.token_urlsafe(32)}),
}.items():
    target = root / name
    if not target.exists():
        target.write_text(value, encoding="utf-8")
print("Local credentials ready; no secrets printed.")

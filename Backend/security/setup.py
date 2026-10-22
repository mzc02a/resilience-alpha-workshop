"""Generate local-only credentials without printing them. Run before Compose."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent / "runtime"
root.mkdir(exist_ok=True)
for name, value in {
    "control-token.txt": secrets.token_urlsafe(32),
}.items():
    target = root / name
    if not target.exists():
        target.write_text(value, encoding="utf-8")
print("Local credentials ready; no secrets printed.")
print("Prepare the probe facial session separately with cyber/prepare-lab.ps1.")

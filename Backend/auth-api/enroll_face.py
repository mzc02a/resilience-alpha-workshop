"""Trusted local enrollment or replacement for an existing user/admin account."""
import argparse
import json
from pathlib import Path

from face_auth import MAX_IMAGE_BYTES, feature


def main():
    parser = argparse.ArgumentParser(description="Enrolement facial local d'un compte existant")
    parser.add_argument("--user-id", required=True, help="UUID du compte existant")
    parser.add_argument("--image", required=True, help="Portrait JPEG ou PNG")
    parser.add_argument("--replace", action="store_true", help="Remplacer la reference apres verification humaine")
    args = parser.parse_args()
    from sqlalchemy import select
    from main import SessionLocal, User, FaceReference, has_face_reference
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.id == args.user_id).with_for_update())
        if user is None or user.role not in ("user", "admin"):
            parser.error("Le compte doit exister")
        if has_face_reference(db, user.id) and not args.replace:
            parser.error("Reference existante ; utiliser --replace pour un remplacement volontaire")
        with Path(args.image).open("rb") as source:
            data = source.read(MAX_IMAGE_BYTES + 1)
        embedding = json.dumps(feature(data).reshape(-1).tolist(), allow_nan=False)
        reference = db.get(FaceReference, user.id)
        if reference is None:
            db.add(FaceReference(user_id=user.id, embedding=embedding))
        else:
            reference.embedding = embedding
        db.commit()
    print("Reference enregistree en base. La photo source n'a pas ete copiee.")


if __name__ == "__main__":
    main()

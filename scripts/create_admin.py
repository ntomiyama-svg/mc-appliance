#!/usr/bin/env python3
"""Create the initial mc-appliance administrator (v0.1.2).

Interactive usage (run from the repo root, inside the venv):

    python scripts/create_admin.py

You will be prompted for a username and a password (entered twice, hidden).
The password is hashed with bcrypt before being stored in the ``admins`` table
of the SQLite database; the plaintext is never written to disk.

If an admin with the same username already exists, the script exits with an
error and changes nothing. v0.1.2 has no UI to edit or delete admins, so create
the account you intend to use.

Like the web app, this script does NOT require root — run it as the same user
that runs mc-appliance.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

# Allow `python scripts/create_admin.py` from the repo root by putting the
# project root (parent of this scripts/ dir) on sys.path.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.auth import hash_password  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Admin  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def main() -> int:
    # Ensure the admins table (and the rest) exists before we touch it.
    init_db()

    username = input("Admin username: ").strip()
    if not username:
        print("Error: username must not be empty.", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        if db.query(Admin).filter(Admin.username == username).first() is not None:
            print(
                f"Error: an admin named '{username}' already exists.",
                file=sys.stderr,
            )
            return 1

        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(
                f"Error: password must be at least {MIN_PASSWORD_LENGTH} characters.",
                file=sys.stderr,
            )
            return 1
        if getpass.getpass("Confirm password: ") != password:
            print("Error: passwords do not match.", file=sys.stderr)
            return 1

        db.add(Admin(username=username, password_hash=hash_password(password)))
        db.commit()
        print(f"Created admin '{username}'. You can now sign in at /login.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

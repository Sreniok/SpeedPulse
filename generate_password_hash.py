#!/usr/bin/env python3
"""Generate pbkdf2 password hash for DASHBOARD_PASSWORD_HASH."""

from __future__ import annotations

import getpass
import hashlib
import os
import sys


def make_hash(password: str, iterations: int = 390000) -> str:
    salt_hex = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations).hex()
    # Use ":" separators so docker-compose .env parsing does not treat hash as variable interpolation.
    return f"pbkdf2_sha256:{iterations}:{salt_hex}:{digest}"


def main() -> int:
    password = getpass.getpass("New dashboard password: ")
    confirm = getpass.getpass("Confirm password: ")

    if not password:
        print("Password cannot be empty", file=sys.stderr)
        return 1

    if password != confirm:
        print("Passwords do not match", file=sys.stderr)
        return 1

    hashed = make_hash(password)
    print("\nUse this in .env:")
    print(f"DASHBOARD_PASSWORD_HASH={hashed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

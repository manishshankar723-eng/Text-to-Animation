"""
seed_admin.py — Create (or reset) a user in the MongoDB auth store.

Handy for bootstrapping the first account so you can log in to the API without
hitting /auth/register, or for resetting a forgotten password from the CLI.

Usage:
    # Create a user (prompts for password if --password omitted):
    python seed_admin.py --email admin@example.com

    # Create with an inline password:
    python seed_admin.py --email admin@example.com --password "s3cret-pass"

    # Reset an existing user's password:
    python seed_admin.py --email admin@example.com --update-password

Requires MongoDB reachable at MONGODB_URI (see .env). Password must be >= 8 chars.
"""

import argparse
import getpass
import sys

from dotenv import load_dotenv

load_dotenv()

from api import security, users  # noqa: E402

MIN_PASSWORD_LEN = 8


def _prompt_password() -> str:
    """Prompt twice for a password (hidden input) and confirm they match."""
    pw = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw != pw2:
        print("Passwords do not match.")
        sys.exit(1)
    return pw


def main():
    parser = argparse.ArgumentParser(
        description="Create or reset a user in the MongoDB auth store.",
    )
    parser.add_argument("--email", required=True, help="User email address.")
    parser.add_argument(
        "--password",
        help="Password (>= 8 chars). If omitted, you'll be prompted securely.",
    )
    parser.add_argument(
        "--update-password", action="store_true",
        help="If the user already exists, reset their password instead of failing.",
    )
    args = parser.parse_args()

    password = args.password or _prompt_password()
    if len(password) < MIN_PASSWORD_LEN:
        print(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
        sys.exit(1)

    # Fail fast with a clear message if MongoDB isn't reachable.
    conn = users.check_connection()
    if not conn["connected"]:
        print(f"Cannot reach MongoDB (db={conn['db']}): {conn['error']}")
        print("Is MongoDB running? Check MONGODB_URI in your .env.")
        sys.exit(1)

    password_hash = security.hash_password(password)
    existing = users.get_user_by_email(args.email)

    if existing:
        if not args.update_password:
            print(
                f"User already exists: {args.email}\n"
                "Pass --update-password to reset their password."
            )
            sys.exit(1)
        users.update_password(args.email, password_hash)
        print(f"Password updated for: {args.email}")
    else:
        user = users.create_user(args.email, password_hash)
        print(f"Created user: {user['email']}")

    print("You can now log in via POST /auth/login.")


if __name__ == "__main__":
    main()

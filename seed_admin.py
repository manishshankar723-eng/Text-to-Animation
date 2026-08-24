"""
seed_admin.py — Create (or reset) a user in the MongoDB auth store, and grant
or revoke the administrator role.

Handy for bootstrapping the first account so you can log in to the API without
hitting /auth/register, for resetting a forgotten password from the CLI, and —
since the admin panel shipped — for making that first account an administrator.

⚠ THE NAME USED TO BE A LIE. Until the admin panel there was no such thing as an
administrator, and this script created an ordinary user like any other. `--role
admin` is what finally makes it do what it has always been called.

Usage:
    # Create a user (prompts for password if --password omitted):
    python seed_admin.py --email admin@example.com

    # Create the first ADMINISTRATOR:
    python seed_admin.py --email admin@example.com --role admin

    # Promote (or demote) somebody who already has an account:
    python seed_admin.py --email someone@example.com --role admin
    python seed_admin.py --email someone@example.com --role user

    # Reset an existing user's password:
    python seed_admin.py --email admin@example.com --update-password

There is a second way in, for when the database is unreachable or the last admin
has been demoted: put the address in `ADMIN_EMAILS` in `.env` and restart the
API. That is a floor the stored role cannot lower — see `users.role_of`.

Requires MongoDB reachable at MONGODB_URI (see .env). Password must be >= 8 chars.
"""

import argparse
import getpass
import sys

from dotenv import load_dotenv

load_dotenv()

from server import security, users  # noqa: E402

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
    parser.add_argument(
        "--role", choices=users.ROLES, default=None,
        help=(
            "Set the account role. 'admin' unlocks the admin panel; 'user' "
            "revokes it. Omitted, an existing account's role is left alone and "
            "a new one is created as an ordinary user."
        ),
    )
    args = parser.parse_args()

    # ⚠ A ROLE CHANGE ON ITS OWN NEEDS NO PASSWORD. Prompting for one to promote
    # an existing colleague would mean either resetting their password to do it
    # or inventing one that is then thrown away.
    existing_first = users.get_user_by_email(args.email)
    if args.role and existing_first and not args.update_password and not args.password:
        conn = users.check_connection()
        if not conn["connected"]:
            print(f"Cannot reach MongoDB (db={conn['db']}): {conn['error']}")
            sys.exit(1)
        users.set_role(args.email, args.role)
        print(f"Role for {args.email.strip().lower()} is now: {args.role}")
        if args.role == users.ROLE_ADMIN:
            print("They will see the Admin panel in the account menu next time they sign in.")
        else:
            print("⚠ Any session they already hold keeps working for up to 30s.")
        return

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

    if args.role:
        users.set_role(args.email, args.role)
        print(f"Role set to: {args.role}")

    print("You can now log in via POST /auth/login.")
    if args.role == users.ROLE_ADMIN:
        print("The Admin panel is in the account menu, bottom-left.")


if __name__ == "__main__":
    main()

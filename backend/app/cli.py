"""Operator commands. There is deliberately no API route that grants the admin role.

Usage (from backend/):
    python -m app.cli create-admin --email admin@example.com     # prompts for a password
    python -m app.cli promote --email someone@example.com
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.auth.service import AuthService, ClientInfo, normalize_email
from app.auth.tokens import resolve_jwt_secret
from app.config import get_settings
from app.core.exceptions import AppError
from app.db.audit import add_audit_log
from app.db.models import User
from app.db.session import create_db_engine, create_session_factory


def _set_admin(session_factory, email: str) -> bool:
    with session_factory.begin() as session:
        user = session.scalar(select(User).where(User.email == normalize_email(email)))
        if user is None:
            return False
        user.role = "admin"
        add_audit_log(session, "user.promote_admin", target_type="user", target_id=user.id, details={"via": "cli"})
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="Register a new account with the admin role")
    create.add_argument("--email", required=True)
    promote = sub.add_parser("promote", help="Give an existing account the admin role")
    promote.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        if args.command == "create-admin":
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Repeat password: "):
                print("Passwords do not match.", file=sys.stderr)
                return 1
            auth = AuthService(session_factory, settings, resolve_jwt_secret(settings))
            auth.register(args.email, password, ClientInfo(user_agent="cli"))
        if not _set_admin(session_factory, args.email):
            print(f"No account found for {args.email}.", file=sys.stderr)
            return 1
        print(f"{args.email} is now an admin.")
        return 0
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())

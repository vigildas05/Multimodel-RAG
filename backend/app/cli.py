"""Operator commands. There is deliberately no API route that grants the admin role.

Usage (from backend/):
    python -m app.cli create-admin --email admin@example.com     # prompts for a password
    python -m app.cli promote --email someone@example.com
    python -m app.cli seed-demo [--samples-dir ../examples] [--force]
    python -m app.cli cleanup-demo
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from sqlalchemy import select

from app.auth.service import AuthService, ClientInfo, normalize_email
from app.auth.tokens import resolve_jwt_secret
from app.config import get_settings
from app.core.exceptions import AppError
from app.core.file_validation import new_document_id, sanitize_filename
from app.ingestion.validation import validate_upload
from app.db.audit import add_audit_log
from app.db.models import User
from app.db.session import create_db_engine, create_session_factory
from app.models.document import DocumentRecord
from app.services.document_service import now_iso
from app.storage.document_registry import DocumentRegistry
from app.storage.upload_store import UploadStore

DEFAULT_SAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"


def _set_admin(session_factory, email: str) -> bool:
    with session_factory.begin() as session:
        user = session.scalar(select(User).where(User.email == normalize_email(email)))
        if user is None:
            return False
        user.role = "admin"
        add_audit_log(session, "user.promote_admin", target_type="user", target_id=user.id, details={"via": "cli"})
        return True


def _admin(args, settings, session_factory) -> int:
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


async def _seed_demo(args, settings, session_factory) -> int:
    from app.services.container import build_services  # loads every model; only needed here

    services = build_services(settings, session_factory)
    scope = services.demo_service.ensure_template_scope()
    registry = DocumentRegistry(session_factory, scope.workspace_id)
    if registry.count() and not args.force:
        print(f"Demo template already has {registry.count()} documents (use --force to re-seed).")
        return 0
    if args.force:
        await services.document_service.reset_workspace(scope)

    samples = sorted(p for p in Path(args.samples_dir).iterdir() if p.is_file())
    failures = 0
    for path in samples:
        try:
            file_type = validate_upload(path.name, path.read_bytes(), settings)  # same checks as uploads
        except AppError as exc:
            print(f"  skipped {path.name}: {exc.message}")
            continue
        document_id, filename = new_document_id(), sanitize_filename(path.name)
        registry.create(
            DocumentRecord(
                document_id=document_id,
                filename=filename,
                file_type=file_type,
                status="processing",
                upload_timestamp=now_iso(),
            )
        )
        try:
            record = await services.document_service.ingest_file(scope, document_id, filename, file_type, path)
            print(f"  ok      {filename}: {record.num_chunks} chunks")
        except AppError as exc:
            failures += 1
            print(f"  FAILED  {filename}: {exc.message} ({exc.detail})", file=sys.stderr)
    print(f"Demo template ready with {registry.count() - failures} of {registry.count()} documents.")
    return 1 if failures else 0


def _cleanup_demo(settings, session_factory) -> int:
    from app.services.demo_service import DemoService
    from app.services.vector_store_service import VectorStoreService

    vector_store = VectorStoreService(
        settings.QDRANT_HOST,
        settings.QDRANT_PORT,
        settings.QDRANT_COLLECTION,
        settings.EMBEDDING_DIM,
        url=settings.QDRANT_URL or None,
        api_key=settings.QDRANT_API_KEY or None,
    )
    auth = AuthService(session_factory, settings, resolve_jwt_secret(settings))
    upload_store = UploadStore(Path(settings.UPLOAD_DIR))
    removed = DemoService(session_factory, vector_store, auth, settings, upload_store).cleanup_expired()
    print(f"Removed {removed} expired demo workspaces.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="Register a new account with the admin role")
    create.add_argument("--email", required=True)
    promote = sub.add_parser("promote", help="Give an existing account the admin role")
    promote.add_argument("--email", required=True)
    seed = sub.add_parser("seed-demo", help="Ingest the sample files into the demo template workspace")
    seed.add_argument("--samples-dir", default=str(DEFAULT_SAMPLES_DIR))
    seed.add_argument("--force", action="store_true", help="Delete and re-ingest the template's documents")
    sub.add_parser("cleanup-demo", help="Delete expired demo workspaces")
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        if args.command in ("create-admin", "promote"):
            return _admin(args, settings, session_factory)
        if args.command == "seed-demo":
            return asyncio.run(_seed_demo(args, settings, session_factory))
        return _cleanup_demo(settings, session_factory)
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())

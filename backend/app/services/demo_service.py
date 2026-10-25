"""Ephemeral demo workspaces ("Try the demo").

Each visitor gets a brand-new guest account and a private workspace that is a copy of the
demo *template* workspace (the sample documents), expiring after DEMO_TTL_HOURS.

Why per-visitor copies instead of one shared demo account: a shared, writable workspace
would let one visitor upload a poisoned document (prompt injection) that the next visitor's
questions then retrieve. Copies isolate visitors from each other, and the template itself
is owned by a system user that can't sign in, so no visitor can ever modify it.

Copying is cheap: document rows are duplicated and Qdrant points are copied with their
existing vectors (no OCR, transcription or embedding at demo start).
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.service import AuthService, ClientInfo, IssuedSession
from app.config import Settings
from app.core.exceptions import DemoUnavailableError, QuotaExceededError
from app.core.tenancy import TenantScope
from app.db.audit import add_audit_log
from app.db.models import AuditLog, Document, User, Workspace, WorkspaceMember
from app.services.vector_store_service import VectorStoreService

logger = logging.getLogger("app.services.demo")

DEMO_TEMPLATE_EMAIL = "demo-template@sentinel.invalid"
DEMO_GUEST_EMAIL_DOMAIN = "demo.sentinel.invalid"
# Not a valid argon2 hash: no password ever verifies, so these accounts can't sign in.
UNUSABLE_PASSWORD_HASH = "!"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DemoService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        vector_store: VectorStoreService,
        auth_service: AuthService,
        settings: Settings,
    ):
        self._session_factory = session_factory
        self._vector_store = vector_store
        self._auth_service = auth_service
        self._settings = settings

    # --- template ----------------------------------------------------------------------

    def template_scope(self) -> TenantScope | None:
        with self._session_factory() as session:
            row = session.execute(
                select(Workspace.id, User.id)
                .join(User, User.id == Workspace.owner_id)
                .where(User.email == DEMO_TEMPLATE_EMAIL)
            ).first()
        return TenantScope(workspace_id=row[0], user_id=row[1]) if row else None

    def ensure_template_scope(self) -> TenantScope:
        """Create the system-owned template workspace if it doesn't exist yet."""
        existing = self.template_scope()
        if existing is not None:
            return existing
        with self._session_factory.begin() as session:
            owner = User(email=DEMO_TEMPLATE_EMAIL, password_hash=UNUSABLE_PASSWORD_HASH, is_active=False)
            session.add(owner)
            session.flush()
            workspace = Workspace(name="Demo template", owner_id=owner.id)
            session.add(workspace)
            session.flush()
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="owner"))
            return TenantScope(workspace_id=workspace.id, user_id=owner.id)

    # --- starting a demo ---------------------------------------------------------------

    def start(self, client: ClientInfo) -> IssuedSession:
        if not self._settings.DEMO_ENABLED:
            raise DemoUnavailableError("The demo is currently disabled.")
        # Opportunistic, bounded cleanup so expired guests don't pile up between cron runs.
        self.cleanup_expired(limit=20)

        template = self.template_scope()
        template_docs = self._ready_documents(template) if template else []
        if not template_docs:
            raise DemoUnavailableError(
                "The demo hasn't been set up yet.",
                detail="An operator needs to run `python -m app.cli seed-demo`.",
            )

        user_id, workspace_id, id_map = self._create_guest(client, template_docs)
        target = TenantScope(workspace_id=workspace_id, user_id=user_id)
        try:
            copied = self._vector_store.clone_documents(template, target, id_map)
        except Exception:
            self._delete_guest(target)
            raise
        logger.info("Started demo workspace %s (%d documents, %d chunks)", workspace_id, len(id_map), copied)

        with self._session_factory.begin() as session:
            user = session.get(User, user_id)
            workspace = session.get(Workspace, workspace_id)
            return self._auth_service.issue_session(session, user, workspace, client)

    def _create_guest(self, client: ClientInfo, template_docs: list[Document]) -> tuple[str, str, dict[str, str]]:
        now = _now()
        with self._session_factory.begin() as session:
            active = session.scalar(
                select(func.count()).select_from(Workspace).where(Workspace.is_demo.is_(True), Workspace.expires_at > now)
            )
            if active >= self._settings.DEMO_MAX_ACTIVE_WORKSPACES:
                raise DemoUnavailableError("The demo is at capacity right now. Please try again later.")
            if client.ip_address:
                recent = session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "demo.start",
                        AuditLog.ip_address == client.ip_address,
                        AuditLog.created_at > now - timedelta(hours=1),
                    )
                )
                if recent >= self._settings.DEMO_MAX_STARTS_PER_IP_PER_HOUR:
                    raise QuotaExceededError("Too many demo sessions from your network. Please try again later.")

            user = User(
                email=f"guest-{secrets.token_hex(6)}@{DEMO_GUEST_EMAIL_DOMAIN}",
                password_hash=UNUSABLE_PASSWORD_HASH,
                is_demo=True,
            )
            session.add(user)
            session.flush()
            workspace = Workspace(
                name="Demo workspace",
                owner_id=user.id,
                is_demo=True,
                expires_at=now + timedelta(hours=self._settings.DEMO_TTL_HOURS),
                corpus_version=1,
            )
            session.add(workspace)
            session.flush()
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))

            id_map: dict[str, str] = {}
            for doc in template_docs:
                new_id = str(uuid.uuid4())
                id_map[doc.id] = new_id
                session.add(
                    Document(
                        id=new_id,
                        workspace_id=workspace.id,
                        filename=doc.filename,
                        file_type=doc.file_type,
                        status="ready",
                        size_bytes=doc.size_bytes,
                        sha256=doc.sha256,
                        num_chunks=doc.num_chunks,
                        num_pages=doc.num_pages,
                        duration_seconds=doc.duration_seconds,
                        used_ocr=doc.used_ocr,
                    )
                )
            add_audit_log(
                session,
                "demo.start",
                actor_user_id=user.id,
                workspace_id=workspace.id,
                ip_address=client.ip_address,
                details={"documents": len(id_map)},
            )
            return user.id, workspace.id, id_map

    def _ready_documents(self, scope: TenantScope) -> list[Document]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(Document)
                    .where(Document.workspace_id == scope.workspace_id, Document.status == "ready")
                    .order_by(Document.created_at)
                )
            )

    # --- cleanup -----------------------------------------------------------------------

    def cleanup_expired(self, limit: int | None = None) -> int:
        """Delete expired demo workspaces: Qdrant points first, then the guest account
        (cascading to its workspace, documents, tokens and quota rows). If Qdrant fails,
        the rows stay so the next run retries."""
        with self._session_factory() as session:
            query = (
                select(Workspace.id, Workspace.owner_id)
                .where(Workspace.is_demo.is_(True), Workspace.expires_at <= _now())
                .order_by(Workspace.expires_at)
            )
            if limit is not None:
                query = query.limit(limit)
            expired = session.execute(query).all()

        removed = 0
        for workspace_id, owner_id in expired:
            try:
                self._delete_guest(TenantScope(workspace_id=workspace_id, user_id=owner_id))
                removed += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed to clean up demo workspace %s; will retry", workspace_id)
        if removed:
            logger.info("Cleaned up %d expired demo workspaces", removed)
        return removed

    def _delete_guest(self, scope: TenantScope) -> None:
        self._vector_store.delete_workspace(scope)
        with self._session_factory.begin() as session:
            user = session.get(User, scope.user_id)
            # Guard: only ever delete demo guests through this path.
            if user is not None and user.is_demo:
                session.delete(user)

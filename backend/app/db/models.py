"""ORM models. Postgres is the source of truth for users, tenancy, document metadata
and the security/audit trail; Qdrant holds only chunk vectors and their payloads.

Conventions:
- Primary keys of user-facing rows are random UUIDs (stored as strings in Python), so
  IDs can't be enumerated or guessed sequentially.
- Enumerated columns are plain strings guarded by CHECK constraints rather than native
  Postgres ENUM types, which are painful to alter in migrations.
- Log tables (security_events, audit_log, query_logs) use bigint identity keys; they are
  append-only and never exposed by ID to tenants.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

USER_ROLES = ("user", "admin")
WORKSPACE_MEMBER_ROLES = ("owner", "member")
DOCUMENT_FILE_TYPES = ("pdf", "image", "audio")
DOCUMENT_STATUSES = ("queued", "processing", "ready", "failed")
SECURITY_SEVERITIES = ("info", "low", "medium", "high", "critical")
CREDIT_ENTRY_KINDS = ("grant", "purchase", "spend", "refund", "adjustment")
# created: order made; verified: checkout signature checked (no credits yet);
# captured: webhook confirmed payment and credits granted; failed: payment failed.
PAYMENT_STATUSES = ("created", "verified", "captured", "failed")


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _in_check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid_pk() -> Mapped[str]:
    return mapped_column(Uuid(as_uuid=False), primary_key=True, default=_new_uuid)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in_check("role", USER_ROLES), name="role"),)

    id: Mapped[str] = _uuid_pk()
    # Stored normalized (stripped + lowercased) by the auth layer; unique on that form.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user", server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    """Opaque refresh tokens, stored only as SHA-256 hashes. Every refresh rotates the
    token; all tokens from one login share a family_id so that reuse of an already-
    rotated token can revoke the entire family (stolen-token detection)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )
    user_agent: Mapped[str | None] = mapped_column(String(256))
    ip_address: Mapped[str | None] = mapped_column(String(45))


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Demo workspaces are ephemeral; a cleanup job deletes them after this time.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped whenever the workspace's chunk set changes, so API processes know when their
    # cached per-workspace BM25 index is stale.
    corpus_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = _created_at()


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (CheckConstraint(_in_check("role", WORKSPACE_MEMBER_ROLES), name="role"),)

    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="owner", server_default="owner")
    created_at: Mapped[datetime] = _created_at()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(_in_check("file_type", DOCUMENT_FILE_TYPES), name="file_type"),
        CheckConstraint(_in_check("status", DOCUMENT_STATUSES), name="status"),
        Index("ix_documents_workspace_id_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[str] = _uuid_pk()
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Display name only (already sanitized). Never used to build a filesystem path.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(512))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    num_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    num_pages: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    used_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Text spans removed at ingestion because a reader couldn't see them (see pdf_hidden_text).
    hidden_span_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Chunks the ingest guard quarantined (stored for review, excluded from retrieval).
    quarantined_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UsageQuota(Base):
    """Per-user, per-UTC-day query counter. Incremented atomically with a conditional
    upsert, so concurrent requests can't exceed the limit (see app.services.quota_service)."""

    __tablename__ = "usage_quota"

    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    queries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class CreditAccount(Base):
    """One row per user, used only as a lock target: credit spends take
    SELECT ... FOR UPDATE on it, so concurrent spends for one user run one at a time and
    can never overspend. The balance itself is never stored; see CreditLedger."""

    __tablename__ = "credit_accounts"

    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = _created_at()


class CreditLedger(Base):
    """Append-only credit history; a user's balance is SUM(amount). A database trigger
    rejects UPDATEs (migration 0005). `idempotency_key` is unique, so a purchase or refund
    can be recorded at most once no matter how many times a webhook or job retries."""

    __tablename__ = "credit_ledger"
    __table_args__ = (
        CheckConstraint(_in_check("kind", CREDIT_ENTRY_KINDS), name="kind"),
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        CheckConstraint("(kind IN ('spend') AND amount < 0) OR (kind <> 'spend' AND amount > 0) OR kind = 'adjustment'", name="amount_sign"),
        Index("ix_credit_ledger_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # e.g. signup_bonus, demo_bonus, query, image_processing, audio_processing, purchase
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(32))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = _created_at()


class Payment(Base):
    """A Razorpay order and what happened to it. The amount is fixed server-side from the
    pack when the order is created; credits are granted only from a verified webhook."""

    __tablename__ = "payments"
    __table_args__ = (CheckConstraint(_in_check("status", PAYMENT_STATUSES), name="status"),)

    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    razorpay_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    pack_id: Mapped[str] = mapped_column(String(32), nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created", server_default="created")
    test_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WebhookEvent(Base):
    """Every verified webhook delivery, keyed by the provider's event ID, so a retried or
    duplicated delivery is recognised and acknowledged without being processed twice."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    received_at: Mapped[datetime] = _created_at()


class SecurityEvent(Base):
    """Every security-relevant decision: blocked inputs, quarantined chunks, canary leaks,
    PII redactions, rejected uploads, auth failures. Feeds the admin dashboard."""

    __tablename__ = "security_events"
    __table_args__ = (CheckConstraint(_in_check("severity", SECURITY_SEVERITIES), name="severity"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    # e.g. input_blocked, chunk_quarantined, canary_leak, pii_redacted, upload_rejected
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    # e.g. input_guard, ingest_guard, output_guard, upload, auth
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info", server_default="info")
    risk_score: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class AuditLog(Base):
    """Who did what: account and document lifecycle actions, admin actions."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))


class QueryLog(Base):
    """Per-query usage and latency metrics. Deliberately does NOT store the question text
    (only its length and hash) — the raw text of *blocked* queries is kept, truncated, in
    security_events where it's needed for review."""

    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    question_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    question_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    num_chunks: Mapped[int | None] = mapped_column(Integer)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer)
    security_ms: Mapped[int | None] = mapped_column(Integer)
    generation_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    llm_model: Mapped[str | None] = mapped_column(String(64))

"""Helpers for the append-only audit_log and security_events tables. Both take an open
session so the record commits (or rolls back) together with the action it describes."""

from sqlalchemy.orm import Session

from app.db.models import AuditLog, SecurityEvent


def add_audit_log(
    session: Session,
    action: str,
    *,
    actor_user_id: str | None = None,
    workspace_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip_address: str | None = None,
    details: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            details=details or {},
        )
    )


def add_security_event(
    session: Session,
    event_type: str,
    source: str,
    *,
    severity: str = "info",
    workspace_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    risk_score: float | None = None,
    details: dict | None = None,
) -> None:
    session.add(
        SecurityEvent(
            event_type=event_type,
            source=source,
            severity=severity,
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=request_id,
            risk_score=risk_score,
            details=details or {},
        )
    )

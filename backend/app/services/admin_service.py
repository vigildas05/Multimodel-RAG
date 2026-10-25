"""Read-only queries behind the admin dashboard: security events, usage and the red-team
report. Nothing here writes; every route that calls it is admin-only (routers/admin.py)."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import CreditLedger, QueryLog, SecurityEvent, User

# Report files are addressed by name, never by path: only `<name>.json` directly inside the
# reports directory, where the name matches this pattern, can be read.
REPORT_NAME = re.compile(r"^[a-z0-9_]{1,64}$")
DEFAULT_REPORT = "full"


@dataclass(frozen=True)
class EventFilters:
    event_type: str | None = None
    source: str | None = None
    severity: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class AdminService:
    def __init__(self, session_factory: sessionmaker[Session], reports_dir: str | Path):
        self._sf = session_factory
        self._reports_dir = Path(reports_dir)

    # --- security events ----------------------------------------------------------------

    def security_events(self, filters: EventFilters, limit: int, offset: int) -> tuple[int, list[dict]]:
        conditions = self._event_conditions(filters)
        with self._sf() as session:
            total = session.scalar(select(func.count()).select_from(SecurityEvent).where(*conditions)) or 0
            rows = session.execute(
                select(SecurityEvent, User.email)
                .outerjoin(User, User.id == SecurityEvent.user_id)
                .where(*conditions)
                .order_by(SecurityEvent.created_at.desc(), SecurityEvent.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        return total, [_event_dict(event, email) for event, email in rows]

    def security_summary(self, days: int) -> dict:
        since = _days_ago(days)
        recent = SecurityEvent.created_at >= since
        day = _utc_day(SecurityEvent.created_at)
        with self._sf() as session:
            by_type = session.execute(
                select(SecurityEvent.event_type, SecurityEvent.source, func.count())
                .where(recent)
                .group_by(SecurityEvent.event_type, SecurityEvent.source)
                .order_by(func.count().desc())
            ).all()
            by_severity = session.execute(
                select(SecurityEvent.severity, func.count()).where(recent).group_by(SecurityEvent.severity)
            ).all()
            by_day = session.execute(
                select(day, SecurityEvent.severity, func.count()).where(recent).group_by(day, SecurityEvent.severity)
            ).all()
            # Distinct values ever seen, for the filter dropdowns.
            types = session.scalars(select(SecurityEvent.event_type).distinct().order_by(SecurityEvent.event_type)).all()
            sources = session.scalars(select(SecurityEvent.source).distinct().order_by(SecurityEvent.source)).all()

        series: dict[str, dict[str, int]] = {d.isoformat(): {} for d in _date_range(days)}
        for d, severity, count in by_day:
            series.setdefault(d.isoformat(), {})[severity] = count
        return {
            "days": days,
            "total": sum(count for _, count in by_severity),
            "by_type": [{"event_type": t, "source": s, "count": c} for t, s, c in by_type],
            "by_severity": {severity: count for severity, count in by_severity},
            "by_day": [{"date": d, "counts": counts} for d, counts in sorted(series.items())],
            "event_types": list(types),
            "sources": list(sources),
        }

    @staticmethod
    def _event_conditions(f: EventFilters) -> list:
        conditions = []
        if f.event_type:
            conditions.append(SecurityEvent.event_type == f.event_type)
        if f.source:
            conditions.append(SecurityEvent.source == f.source)
        if f.severity:
            conditions.append(SecurityEvent.severity == f.severity)
        if f.user_id:
            conditions.append(SecurityEvent.user_id == f.user_id)
        if f.workspace_id:
            conditions.append(SecurityEvent.workspace_id == f.workspace_id)
        if f.since:
            conditions.append(SecurityEvent.created_at >= f.since)
        if f.until:
            conditions.append(SecurityEvent.created_at < f.until)
        return conditions

    # --- usage --------------------------------------------------------------------------

    def usage(self, days: int) -> dict:
        since = _days_ago(days)
        q_day = _utc_day(QueryLog.created_at)
        l_day = _utc_day(CreditLedger.created_at)
        p95 = func.percentile_cont(0.95).within_group(QueryLog.total_ms)
        with self._sf() as session:
            per_day = session.execute(
                select(
                    q_day,
                    func.count(),
                    func.count().filter(QueryLog.blocked),
                    func.avg(QueryLog.total_ms),
                    p95,
                    func.coalesce(func.sum(QueryLog.prompt_tokens), 0),
                    func.coalesce(func.sum(QueryLog.completion_tokens), 0),
                )
                .where(QueryLog.created_at >= since)
                .group_by(q_day)
            ).all()
            spent = session.execute(
                select(l_day, func.coalesce(func.sum(-CreditLedger.amount), 0))
                # Net of refunds: a failed job's charge given back was never really spent.
                .where(CreditLedger.created_at >= since, CreditLedger.kind.in_(("spend", "refund")))
                .group_by(l_day)
            ).all()
            totals = session.execute(
                select(
                    func.count(),
                    func.count().filter(QueryLog.blocked),
                    func.count(func.distinct(QueryLog.user_id)),
                    func.avg(QueryLog.total_ms),
                    p95,
                    func.avg(QueryLog.retrieval_ms),
                    func.avg(QueryLog.security_ms),
                    func.avg(QueryLog.generation_ms),
                    func.coalesce(func.sum(QueryLog.prompt_tokens), 0),
                    func.coalesce(func.sum(QueryLog.completion_tokens), 0),
                ).where(QueryLog.created_at >= since)
            ).one()
            credits_by_reason = session.execute(
                select(CreditLedger.kind, CreditLedger.reason, func.sum(CreditLedger.amount))
                .where(CreditLedger.created_at >= since)
                .group_by(CreditLedger.kind, CreditLedger.reason)
                .order_by(CreditLedger.kind, CreditLedger.reason)
            ).all()
            top_users = session.execute(
                select(User.email, func.count(), func.count().filter(QueryLog.blocked))
                .join(User, User.id == QueryLog.user_id)
                .where(QueryLog.created_at >= since)
                .group_by(User.email)
                .order_by(func.count().desc())
                .limit(10)
            ).all()
            models = session.execute(
                select(QueryLog.llm_provider, QueryLog.llm_model, func.count())
                .where(QueryLog.created_at >= since, QueryLog.llm_model.is_not(None))
                .group_by(QueryLog.llm_provider, QueryLog.llm_model)
            ).all()

        spent_by_day = {d.isoformat(): int(v) for d, v in spent}
        series = {
            d.isoformat(): {"queries": 0, "blocked": 0, "mean_ms": None, "p95_ms": None, "tokens": 0}
            for d in _date_range(days)
        }
        for d, n, blocked, mean, p95_ms, pt, ct in per_day:
            series[d.isoformat()] = {
                "queries": n,
                "blocked": blocked,
                "mean_ms": _round(mean),
                "p95_ms": _round(p95_ms),
                "tokens": int(pt) + int(ct),
            }
        n, blocked, users, mean, p95_ms, retrieval, security, generation, pt, ct = totals
        return {
            "days": days,
            "totals": {
                "queries": n,
                "blocked": blocked,
                "active_users": users,
                "credits_spent": sum(spent_by_day.values()),
                "prompt_tokens": int(pt),
                "completion_tokens": int(ct),
            },
            "latency_ms": {
                "mean": _round(mean),
                "p95": _round(p95_ms),
                "retrieval_mean": _round(retrieval),
                "security_mean": _round(security),
                "generation_mean": _round(generation),
            },
            "by_day": [
                {"date": d, **values, "credits_spent": spent_by_day.get(d, 0)} for d, values in sorted(series.items())
            ],
            "credits_by_reason": [{"kind": k, "reason": r, "amount": int(a)} for k, r, a in credits_by_reason],
            "top_users": [{"email": e, "queries": q, "blocked": b} for e, q, b in top_users],
            "models": [{"provider": p, "model": m, "queries": q} for p, m, q in models],
        }

    # --- red-team reports ---------------------------------------------------------------

    def redteam_reports(self) -> list[str]:
        if not self._reports_dir.is_dir():
            return []
        return sorted(p.stem for p in self._reports_dir.glob("*.json") if REPORT_NAME.match(p.stem))

    def redteam_report(self, name: str = DEFAULT_REPORT) -> dict | None:
        """The report's summary (per-attack records are left out: they are large, and the
        markdown report in the repo already lists notable misses)."""
        if not REPORT_NAME.match(name) or name not in self.redteam_reports():
            return None
        try:
            data = json.loads((self._reports_dir / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        summary = {k: v for k, v in data.items() if k != "records"}
        summary["name"] = name
        summary["misses"] = _misses(data.get("records", {}).get("on", []))
        return summary


def _misses(records: list) -> list[dict]:
    """Attacks that still succeeded with security on: id, category, vector and goal only."""
    if not isinstance(records, list):
        return []
    keys = ("id", "category", "split", "vector", "goal")
    return [{k: r.get(k) for k in keys} for r in records if isinstance(r, dict) and r.get("success")][:200]


def _event_dict(event: SecurityEvent, email: str | None) -> dict:
    return {
        "id": event.id,
        "created_at": event.created_at.isoformat(),
        "event_type": event.event_type,
        "source": event.source,
        "severity": event.severity,
        "risk_score": event.risk_score,
        "workspace_id": event.workspace_id,
        "user_id": event.user_id,
        "user_email": email,
        "request_id": event.request_id,
        "details": event.details or {},
    }


def _utc_day(column):
    # Bucket by UTC day whatever the database session's time zone is.
    return cast(func.timezone("UTC", column), Date)


def _days_ago(days: int) -> datetime:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days - 1)


def _date_range(days: int):
    start = _days_ago(days).date()
    return [start + timedelta(days=i) for i in range(days)]


def _round(value) -> float | None:
    return None if value is None else round(float(value), 1)


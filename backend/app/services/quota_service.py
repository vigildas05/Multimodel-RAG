"""Daily query quota (chat + search), per user, per UTC day.

Consumption is a single conditional upsert:
    INSERT ... ON CONFLICT (user_id, day) DO UPDATE SET queries = queries + 1
    WHERE queries < :limit RETURNING queries
so the check and the increment are one atomic statement, and concurrent requests can't
race past the limit. A request that fails after consuming gets its query refunded.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.auth.service import AuthenticatedUser
from app.config import Settings
from app.core.exceptions import QuotaExceededError
from app.db.models import UsageQuota


@dataclass(frozen=True)
class QuotaTicket:
    day: date
    used: int
    limit: int


def _today() -> date:
    return datetime.now(UTC).date()


class QuotaService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings):
        self._session_factory = session_factory
        self._settings = settings

    def limit_for(self, user: AuthenticatedUser) -> int:
        return self._settings.DEMO_DAILY_QUERY_LIMIT if user.is_demo else self._settings.DAILY_QUERY_LIMIT

    def consume(self, user: AuthenticatedUser) -> QuotaTicket:
        limit, day = self.limit_for(user), _today()
        if limit > 0:
            stmt = (
                insert(UsageQuota)
                .values(user_id=user.user_id, day=day, queries=1)
                .on_conflict_do_update(
                    index_elements=[UsageQuota.user_id, UsageQuota.day],
                    set_={"queries": UsageQuota.queries + 1},
                    where=UsageQuota.queries < limit,
                )
                .returning(UsageQuota.queries)
            )
            with self._session_factory.begin() as session:
                used = session.execute(stmt).scalar_one_or_none()
            if used is not None:
                return QuotaTicket(day=day, used=used, limit=limit)
        raise QuotaExceededError(
            f"You've used all {limit} queries for today.",
            detail="The quota resets at midnight UTC.",
        )

    def refund(self, user_id: str, day: date) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(UsageQuota)
                .where(UsageQuota.user_id == user_id, UsageQuota.day == day, UsageQuota.queries > 0)
                .values(queries=UsageQuota.queries - 1)
            )

    def usage_today(self, user: AuthenticatedUser) -> QuotaTicket:
        day = _today()
        with self._session_factory() as session:
            used = session.scalar(
                select(UsageQuota.queries).where(UsageQuota.user_id == user.user_id, UsageQuota.day == day)
            )
        return QuotaTicket(day=day, used=used or 0, limit=self.limit_for(user))

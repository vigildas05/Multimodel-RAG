"""Credit balances on top of the append-only ledger.

Invariants:
- The balance is always SUM(credit_ledger.amount) for the user; nothing caches it.
- A spend locks the user's credit_accounts row (SELECT ... FOR UPDATE), re-reads the
  balance and only then inserts the negative entry, all in one transaction. Concurrent
  spends for the same user therefore serialize, and the balance can't go below zero.
- Grants, purchases and refunds carry an idempotency key with a unique constraint, so the
  same purchase or refund can never be credited twice, whatever retries.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import InsufficientCreditsError
from app.core.metrics import CREDITS_SPENT
from app.db.models import CreditAccount, CreditLedger


@dataclass(frozen=True)
class Charge:
    """A completed spend; pass it back to refund() if the work then fails."""

    user_id: str
    amount: int
    reason: str
    reference_type: str | None
    reference_id: str | None
    entry_id: int


def ensure_account(session: Session, user_id: str) -> None:
    session.execute(insert(CreditAccount).values(user_id=user_id).on_conflict_do_nothing())


def add_entry(
    session: Session,
    user_id: str,
    amount: int,
    kind: str,
    reason: str,
    *,
    reference_type: str | None = None,
    reference_id: str | None = None,
    idempotency_key: str | None = None,
) -> bool:
    """Insert a ledger entry inside the caller's transaction. Returns False (and inserts
    nothing) if an entry with the same idempotency key already exists."""
    ensure_account(session, user_id)
    stmt = (
        insert(CreditLedger)
        .values(
            user_id=user_id,
            amount=amount,
            kind=kind,
            reason=reason,
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
        )
        .on_conflict_do_nothing(index_elements=[CreditLedger.idempotency_key])
        .returning(CreditLedger.id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def balance_in(session: Session, user_id: str) -> int:
    return session.scalar(select(func.coalesce(func.sum(CreditLedger.amount), 0)).where(CreditLedger.user_id == user_id))


class CreditService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def balance(self, user_id: str) -> int:
        with self._session_factory() as session:
            return balance_in(session, user_id)

    def spend(
        self, user_id: str, amount: int, reason: str, reference_type: str | None = None, reference_id: str | None = None
    ) -> Charge | None:
        """Atomically deduct `amount`, or raise InsufficientCreditsError. Free actions
        (amount 0) succeed without touching the ledger and return None."""
        if amount <= 0:
            return None
        with self._session_factory.begin() as session:
            ensure_account(session, user_id)
            session.execute(select(CreditAccount).where(CreditAccount.user_id == user_id).with_for_update())
            available = balance_in(session, user_id)
            if available < amount:
                raise InsufficientCreditsError(
                    f"This needs {amount} credit{'s' if amount != 1 else ''}, and you have {available}.",
                    detail="Buy more credits on the Billing page.",
                )
            entry = CreditLedger(
                user_id=user_id,
                amount=-amount,
                kind="spend",
                reason=reason,
                reference_type=reference_type,
                reference_id=reference_id,
            )
            session.add(entry)
            session.flush()
            charge = Charge(user_id, amount, reason, reference_type, reference_id, entry.id)
        CREDITS_SPENT.labels(reason).inc(amount)
        return charge

    def refund(self, charge: Charge | None, reason: str = "refund") -> bool:
        """Give a charge back. Idempotent per charge: a second call does nothing."""
        if charge is None:
            return False
        with self._session_factory.begin() as session:
            return add_entry(
                session,
                charge.user_id,
                charge.amount,
                "refund",
                reason,
                reference_type=charge.reference_type,
                reference_id=charge.reference_id,
                idempotency_key=f"refund:{charge.entry_id}",
            )

    def refund_reference(self, user_id: str, reference_type: str, reference_id: str, reason: str) -> bool:
        """Refund the spend recorded against a reference (e.g. a document whose processing
        failed in the worker, long after the request that paid for it). Idempotent."""
        with self._session_factory() as session:
            entry = session.scalar(
                select(CreditLedger).where(
                    CreditLedger.user_id == user_id,
                    CreditLedger.kind == "spend",
                    CreditLedger.reference_type == reference_type,
                    CreditLedger.reference_id == reference_id,
                )
            )
        if entry is None:
            return False
        return self.refund(
            Charge(user_id, -entry.amount, entry.reason, reference_type, reference_id, entry.id), reason=reason
        )

    def grant(self, user_id: str, amount: int, reason: str, idempotency_key: str | None = None) -> bool:
        with self._session_factory.begin() as session:
            return add_entry(session, user_id, amount, "grant", reason, idempotency_key=idempotency_key)

    def history(self, user_id: str, limit: int = 50) -> list[CreditLedger]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(CreditLedger)
                    .where(CreditLedger.user_id == user_id)
                    .order_by(CreditLedger.created_at.desc(), CreditLedger.id.desc())
                    .limit(limit)
                )
            )

"""Per-query usage and latency log (the query_logs table). Stores the question's length and
hash only, never its text."""

import hashlib
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from app.auth.service import AuthenticatedUser
from app.core.metrics import observe_query
from app.db.models import QueryLog


@dataclass
class QueryTimings:
    security_ms: float = 0.0
    retrieval_ms: float | None = None
    generation_ms: float | None = None
    total_ms: float | None = None
    extra: dict = field(default_factory=dict)


class QueryLogService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def log(
        self,
        user: AuthenticatedUser,
        question: str,
        *,
        blocked: bool,
        timings: QueryTimings,
        num_chunks: int | None = None,
        usage: dict | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        usage = usage or {}
        observe_query(blocked=blocked, timings=timings, usage=usage)

        def ms(value: float | None) -> int | None:
            return None if value is None else round(value)

        with self._session_factory.begin() as session:
            session.add(
                QueryLog(
                    workspace_id=user.workspace_id,
                    user_id=user.user_id,
                    question_chars=len(question),
                    question_sha256=hashlib.sha256(question.encode()).hexdigest(),
                    blocked=blocked,
                    num_chunks=num_chunks,
                    security_ms=ms(timings.security_ms),
                    retrieval_ms=ms(timings.retrieval_ms),
                    generation_ms=ms(timings.generation_ms),
                    total_ms=ms(timings.total_ms),
                    prompt_tokens=usage.get("prompt_tokens") or usage.get("prompt_eval_count"),
                    completion_tokens=usage.get("completion_tokens") or usage.get("eval_count"),
                    llm_provider=llm_provider,
                    llm_model=(llm_model or "")[:64] or None,
                )
            )

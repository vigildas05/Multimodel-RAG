from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Workspace


class WorkspaceRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def get_corpus_version(self, workspace_id: str) -> int:
        with self._session_factory() as session:
            return session.scalar(select(Workspace.corpus_version).where(Workspace.id == workspace_id)) or 0

    def bump_corpus_version(self, workspace_id: str) -> None:
        """Atomic increment in SQL, so concurrent ingests never lose an update."""
        with self._session_factory.begin() as session:
            session.execute(
                update(Workspace)
                .where(Workspace.id == workspace_id)
                .values(corpus_version=Workspace.corpus_version + 1)
            )

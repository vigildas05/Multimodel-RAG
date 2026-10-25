"""TEMPORARY until step 1.3 (per-request tenant scope from the authenticated user).

Until auth exists, the app runs as a single local workspace owned by a system user that
cannot log in. This keeps the existing upload/chat flow working on the new schema. Remove
this module once every request carries its own workspace scope.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import User, Workspace, WorkspaceMember

LOCAL_SYSTEM_EMAIL = "local-dev@sentinel.invalid"
LOCAL_WORKSPACE_NAME = "Local development"
# Not a valid argon2 hash, so no password can ever verify against it.
UNUSABLE_PASSWORD_HASH = "!"


def ensure_local_workspace(session_factory: sessionmaker[Session]) -> str:
    """Return the local workspace's ID, creating the system user and workspace if needed."""
    with session_factory.begin() as session:
        user = session.scalar(select(User).where(User.email == LOCAL_SYSTEM_EMAIL))
        if user is None:
            user = User(email=LOCAL_SYSTEM_EMAIL, password_hash=UNUSABLE_PASSWORD_HASH, is_active=False)
            session.add(user)
            session.flush()

        workspace = session.scalar(
            select(Workspace).where(Workspace.owner_id == user.id, Workspace.name == LOCAL_WORKSPACE_NAME)
        )
        if workspace is None:
            workspace = Workspace(name=LOCAL_WORKSPACE_NAME, owner_id=user.id)
            session.add(workspace)
            session.flush()
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        return workspace.id

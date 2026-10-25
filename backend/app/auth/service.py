"""Account lifecycle: register, login, refresh-token rotation, logout, and resolving an
access token back to a live user.

Refresh rotation: each refresh revokes the presented token and issues a new one in the
same family. Presenting an already-rotated token means two parties hold the same token
(e.g. it was stolen), so the whole family is revoked and both are logged out.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import hash_password, needs_rehash, validate_password_policy, verify_password
from app.auth.tokens import create_access_token, decode_access_token, hash_token, new_opaque_token
from app.billing.credits import add_entry
from app.config import Settings
from app.core.exceptions import AuthenticationError, EmailAlreadyRegisteredError
from app.core.tenancy import TenantScope
from app.db.audit import add_audit_log, add_security_event
from app.db.models import RefreshToken, User, Workspace, WorkspaceMember

logger = logging.getLogger("app.auth.service")

INVALID_CREDENTIALS = "Invalid email or password."
SESSION_EXPIRED = "Your session has expired. Please sign in again."


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str
    role: str
    workspace_id: str
    is_demo: bool
    # Set for ephemeral demo workspaces; the session stops working once it passes.
    workspace_expires_at: datetime | None = None

    @property
    def scope(self) -> TenantScope:
        return TenantScope(workspace_id=self.workspace_id, user_id=self.user_id)


@dataclass(frozen=True)
class IssuedSession:
    user: AuthenticatedUser
    access_token: str
    refresh_token: str
    refresh_token_id: str
    csrf_token: str


@dataclass(frozen=True)
class ClientInfo:
    ip_address: str | None = None
    user_agent: str | None = None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def email_fingerprint(email: str) -> str:
    """Lets security events correlate failed logins per account without storing the email."""
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _workspace_is_active():
    """Expired demo workspaces are dead immediately, even before cleanup deletes them."""
    return or_(Workspace.expires_at.is_(None), Workspace.expires_at > func.now())


class AuthService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings, jwt_secret: str):
        self._session_factory = session_factory
        self._settings = settings
        self._jwt_secret = jwt_secret

    # --- registration / login -------------------------------------------------------

    def register(self, email: str, password: str, client: ClientInfo) -> IssuedSession:
        email = normalize_email(email)
        validate_password_policy(password, email, self._settings.PASSWORD_MIN_LENGTH)
        password_hash = hash_password(password)

        try:
            with self._session_factory.begin() as session:
                if session.scalar(select(User.id).where(User.email == email)) is not None:
                    raise EmailAlreadyRegisteredError("An account with this email already exists.")
                user = User(email=email, password_hash=password_hash)
                session.add(user)
                session.flush()
                workspace = Workspace(name="Personal workspace", owner_id=user.id)
                session.add(workspace)
                session.flush()
                session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
                add_audit_log(
                    session,
                    "user.register",
                    actor_user_id=user.id,
                    workspace_id=workspace.id,
                    target_type="user",
                    target_id=user.id,
                    ip_address=client.ip_address,
                )
                if self._settings.SIGNUP_BONUS_CREDITS > 0:
                    add_entry(
                        session,
                        user.id,
                        self._settings.SIGNUP_BONUS_CREDITS,
                        "grant",
                        "signup_bonus",
                        idempotency_key=f"signup:{user.id}",
                    )
                return self.issue_session(session, user, workspace, client)
        except IntegrityError as exc:
            # Lost a race with a concurrent registration of the same email.
            raise EmailAlreadyRegisteredError("An account with this email already exists.") from exc

    def login(self, email: str, password: str, client: ClientInfo) -> IssuedSession:
        email = normalize_email(email)
        with self._session_factory.begin() as session:
            user = session.scalar(select(User).where(User.email == email))
            password_ok = verify_password(user.password_hash if user else None, password)
            workspace = self._active_workspace(session, user.id) if user else None

            if not password_ok or not user.is_active or workspace is None:
                reason = "bad_credentials" if not password_ok else "inactive_or_no_workspace"
                add_security_event(
                    session,
                    "auth_failure",
                    "auth",
                    severity="low",
                    user_id=user.id if user else None,
                    details={"reason": reason, "email_fp": email_fingerprint(email), "ip": client.ip_address},
                )
                failed = True
            else:
                failed = False
                if needs_rehash(user.password_hash):
                    user.password_hash = hash_password(password)
                user.last_login_at = _now()
                add_audit_log(
                    session,
                    "user.login",
                    actor_user_id=user.id,
                    workspace_id=workspace.id,
                    ip_address=client.ip_address,
                )
                issued = self.issue_session(session, user, workspace, client)

        # Raised after the transaction so the auth_failure event is committed, not rolled back.
        if failed:
            raise AuthenticationError(INVALID_CREDENTIALS)
        return issued

    # --- refresh / logout -----------------------------------------------------------

    def refresh(self, raw_refresh_token: str | None, client: ClientInfo) -> IssuedSession:
        if not raw_refresh_token:
            raise AuthenticationError(SESSION_EXPIRED)

        issued: IssuedSession | None = None
        with self._session_factory.begin() as session:
            # Row lock: two concurrent refreshes with the same token serialize here, so
            # at most one rotation succeeds and the other is treated as reuse.
            token = session.scalar(
                select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh_token)).with_for_update()
            )
            if token is None:
                pass
            elif token.revoked_at is not None:
                if token.replaced_by_id is not None:
                    self._revoke_family(session, token.family_id)
                    add_security_event(
                        session,
                        "refresh_token_reuse",
                        "auth",
                        severity="high",
                        user_id=token.user_id,
                        details={"family_id": token.family_id, "ip": client.ip_address},
                    )
                    logger.warning("Refresh token reuse detected; revoked family %s", token.family_id)
            elif token.expires_at > _now():
                user = session.get(User, token.user_id)
                workspace = self._active_workspace(session, user.id) if user else None
                if user is not None and user.is_active and workspace is not None:
                    issued = self.issue_session(session, user, workspace, client, family_id=token.family_id)
                    token.revoked_at = _now()
                    token.replaced_by_id = issued.refresh_token_id

        if issued is None:
            raise AuthenticationError(SESSION_EXPIRED)
        return issued

    def logout(self, raw_refresh_token: str | None, client: ClientInfo) -> None:
        """Revoke the presented token's whole family (this login session). Idempotent."""
        if not raw_refresh_token:
            return
        with self._session_factory.begin() as session:
            token = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh_token)))
            if token is None:
                return
            self._revoke_family(session, token.family_id)
            add_audit_log(session, "user.logout", actor_user_id=token.user_id, ip_address=client.ip_address)

    # --- per-request ----------------------------------------------------------------

    def resolve_access_token(self, access_token: str) -> AuthenticatedUser:
        """Verify the JWT, then re-check the user against the database on every request,
        so deactivation and role changes take effect immediately rather than at token expiry."""
        claims = decode_access_token(access_token, self._jwt_secret, self._settings.JWT_ISSUER)
        with self._session_factory() as session:
            row = session.execute(
                select(User, Workspace)
                .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                .where(
                    User.id == claims.user_id,
                    User.is_active.is_(True),
                    Workspace.id == claims.workspace_id,
                    _workspace_is_active(),
                )
            ).first()
            if row is None:
                raise AuthenticationError("Your session is invalid or has expired.")
            return self._to_authenticated(*row)

    # --- internals ------------------------------------------------------------------

    def issue_session(
        self,
        session: Session,
        user: User,
        workspace: Workspace,
        client: ClientInfo,
        family_id: str | None = None,
    ) -> IssuedSession:
        raw_refresh = new_opaque_token()
        refresh_token_id = str(uuid.uuid4())
        session.add(
            RefreshToken(
                id=refresh_token_id,
                user_id=user.id,
                family_id=family_id or str(uuid.uuid4()),
                token_hash=hash_token(raw_refresh),
                expires_at=_now() + timedelta(days=self._settings.REFRESH_TOKEN_TTL_DAYS),
                user_agent=(client.user_agent or "")[:256] or None,
                ip_address=client.ip_address,
            )
        )
        session.flush()
        access = create_access_token(
            user.id,
            workspace.id,
            self._jwt_secret,
            self._settings.JWT_ISSUER,
            timedelta(minutes=self._settings.ACCESS_TOKEN_TTL_MINUTES),
        )
        return IssuedSession(
            user=self._to_authenticated(user, workspace),
            access_token=access,
            refresh_token=raw_refresh,
            refresh_token_id=refresh_token_id,
            # Rotated with every session issue, so a pre-login token can't be fixed by an attacker.
            csrf_token=new_opaque_token(),
        )

    @staticmethod
    def _revoke_family(session: Session, family_id: str) -> None:
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )

    @staticmethod
    def _active_workspace(session: Session, user_id: str) -> Workspace | None:
        return session.scalar(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user_id, WorkspaceMember.role == "owner", _workspace_is_active())
            .order_by(WorkspaceMember.created_at)
            .limit(1)
        )

    @staticmethod
    def _to_authenticated(user: User, workspace: Workspace) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=user.id,
            email=user.email,
            role=user.role,
            workspace_id=workspace.id,
            is_demo=user.is_demo,
            workspace_expires_at=workspace.expires_at,
        )

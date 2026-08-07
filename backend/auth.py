"""Authentication, sessions, invitations, preferences, and resource access grants.

Authentication is deliberately local and self-contained. The browser receives an opaque
session token in an HttpOnly cookie; only its SHA-256 digest is stored. Passwords use
``hashlib.scrypt`` with a per-password salt. CSRF uses the double-submit pattern: a random
value is placed in a readable SameSite cookie and must be repeated in a request header,
while the server also verifies its digest against the authenticated session.

The first registered account becomes the owner and claims data created by older single-user
builds. Later accounts require an invitation. That makes an upgrade lossless without turning
registration into an unauthenticated account-creation endpoint forever.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON, UniqueConstraint, delete, update
from sqlmodel import Field, Session, SQLModel, col, func, select

Role = Literal["owner", "admin", "member"]
Permission = Literal["viewer", "editor"]
ResourceType = Literal["conversation", "job", "estimate"]

SESSION_COOKIE = "devvy_session"
CSRF_COOKIE = "devvy_csrf"
PASSWORD_SCHEME = "scrypt-v1"
DEFAULT_PREFERENCES = {
    "default_workspace": "home",
    "density": "comfortable",
    "evidence_expanded": True,
    "confirm_external_research": False,
}
_CREATE_USER_LOCK = threading.Lock()


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    """SQLite returns stored UTC datetimes without tzinfo; restore it before comparison."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class User(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, unique=True, max_length=320)
    display_name: str = Field(max_length=100)
    password_hash: str
    role: str = Field(default="member", index=True)
    active: bool = Field(default=True, index=True)
    preferences: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    last_login_at: datetime | None = None


class AuthSession(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(index=True, unique=True, max_length=64)
    csrf_hash: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=now)
    expires_at: datetime = Field(index=True)
    last_seen_at: datetime = Field(default_factory=now)
    revoked_at: datetime | None = Field(default=None, index=True)
    user_agent: str = Field(default="", max_length=300)
    ip_hint: str = Field(default="", max_length=80)


class Invitation(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, max_length=320)
    role: str = Field(default="member")
    token_hash: str = Field(index=True, unique=True, max_length=64)
    invited_by: UUID = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=now)
    expires_at: datetime = Field(index=True)
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None


class ResourceShare(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("resource_type", "resource_id", "recipient_id", name="uq_resource_share"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    resource_type: str = Field(index=True, max_length=30)
    resource_id: str = Field(index=True, max_length=64)
    owner_id: UUID = Field(foreign_key="user.id", index=True)
    recipient_id: UUID = Field(foreign_key="user.id", index=True)
    permission: str = Field(default="viewer", max_length=20)
    created_at: datetime = Field(default_factory=now)


class UploadRecord(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID | None = Field(default=None, foreign_key="user.id", index=True)
    filename: str = Field(max_length=500)
    stored_name: str = Field(unique=True, index=True, max_length=100)
    content_type: str = Field(max_length=200)
    size: int
    created_at: datetime = Field(default_factory=now, index=True)


class ArtifactRecord(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID | None = Field(default=None, foreign_key="user.id", index=True)
    filename: str = Field(unique=True, index=True, max_length=200)
    job_id: UUID | None = Field(default=None, index=True)
    conversation_id: UUID | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=now, index=True)


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if not email or len(email) > 320 or "@" not in email:
        raise ValueError("Enter a valid email address.")
    local, _, domain = email.rpartition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("Enter a valid email address.")
    return email


def validate_password(password: str, email: str = "") -> None:
    if len(password) < 12:
        raise ValueError("Use at least 12 characters for your password.")
    if len(password) > 128:
        raise ValueError("Password must not exceed 128 characters.")
    local = email.partition("@")[0]
    if local and len(local) >= 4 and local.casefold() in password.casefold():
        raise ValueError("Your password must not contain your email name.")
    if password.casefold() in {"password1234", "password123!", "devvydevvy12"}:
        raise ValueError("Choose a less common password.")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "$".join(
        (PASSWORD_SCHEME, base64.urlsafe_b64encode(salt).decode(), base64.urlsafe_b64encode(digest).decode())
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_text, digest_text = stored.split("$", 2)
        if scheme != PASSWORD_SCHEME:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def user_count(engine) -> int:
    with Session(engine) as session:
        return int(session.exec(select(func.count()).select_from(User)).one())


def public_user(user: User) -> dict[str, Any]:
    preferences = {**DEFAULT_PREFERENCES, **(user.preferences or {})}
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "active": user.active,
        "preferences": preferences,
        "created_at": aware(user.created_at).isoformat(),
        "last_login_at": aware(user.last_login_at).isoformat() if user.last_login_at else None,
    }


def claim_legacy_data(engine, owner_id: UUID) -> None:
    """Assign records from pre-authentication builds to the first owner."""
    with engine.begin() as connection:
        for table in (
            "conversation", "message", "job", "estimaterecord", "uploadrecord", "artifactrecord"
        ):
            columns = {
                row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            target = "author_id" if table == "message" else "owner_id"
            if target in columns:
                connection.exec_driver_sql(
                    f"UPDATE {table} SET {target} = ? WHERE {target} IS NULL", (owner_id.hex,)
                )


def record_artifact(
    engine,
    url: str | None,
    *,
    owner_id: UUID | None,
    job_id: UUID | None,
    conversation_id: UUID | None = None,
) -> None:
    if not url or not url.startswith("/generated/"):
        return
    filename = url.removeprefix("/generated/")
    if not filename or "/" in filename or "\\" in filename:
        return
    with Session(engine) as session:
        existing = session.exec(select(ArtifactRecord).where(ArtifactRecord.filename == filename)).first()
        if existing:
            return
        session.add(
            ArtifactRecord(
                owner_id=owner_id,
                filename=filename,
                job_id=job_id,
                conversation_id=conversation_id,
            )
        )
        session.commit()


def _create_user(
    engine,
    *,
    email: str,
    display_name: str,
    password: str,
    invite_token: str | None = None,
) -> User:
    normalized = normalize_email(email)
    name = display_name.strip()
    if len(name) < 2 or len(name) > 100:
        raise ValueError("Display name must be between 2 and 100 characters.")
    validate_password(password, normalized)
    first = user_count(engine) == 0
    invitation: Invitation | None = None
    with Session(engine) as session:
        if session.exec(select(User).where(User.email == normalized)).first():
            raise ValueError("An account with that email already exists.")
        role = "owner" if first else "member"
        if not first:
            if not invite_token:
                raise ValueError("An invitation is required to join this workspace.")
            invitation = session.exec(
                select(Invitation).where(Invitation.token_hash == token_digest(invite_token))
            ).first()
            if (
                invitation is None
                or invitation.email != normalized
                or invitation.accepted_at is not None
                or invitation.revoked_at is not None
                or aware(invitation.expires_at) <= now()
            ):
                raise ValueError("This invitation is invalid or has expired.")
            role = invitation.role
        user = User(
            email=normalized,
            display_name=name,
            password_hash=hash_password(password),
            role=role,
            preferences=dict(DEFAULT_PREFERENCES),
        )
        session.add(user)
        if invitation:
            invitation.accepted_at = now()
            session.add(invitation)
        session.commit()
        session.refresh(user)
    if first:
        claim_legacy_data(engine, user.id)
    return user


def create_user(
    engine,
    *,
    email: str,
    display_name: str,
    password: str,
    invite_token: str | None = None,
) -> User:
    # The app is a single process, but registration requests can still arrive concurrently.
    # Serialize the "is this the first owner?" decision so two first-run tabs cannot both win.
    with _CREATE_USER_LOCK:
        return _create_user(
            engine,
            email=email,
            display_name=display_name,
            password=password,
            invite_token=invite_token,
        )


def authenticate(engine, email: str, password: str) -> User | None:
    try:
        normalized = normalize_email(email)
    except ValueError:
        return None
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == normalized)).first()
        # Run one scrypt calculation even when the account is absent to reduce user-enumeration
        # timing differences. The salt is intentionally constant only for this fake check.
        if user is None:
            hashlib.scrypt(password.encode(), salt=b"devvy-fake-check", n=2**14, r=8, p=1, dklen=32)
            return None
        if not user.active or not verify_password(password, user.password_hash):
            return None
        user.last_login_at = now()
        user.updated_at = now()
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def create_session(
    engine,
    user_id: UUID,
    *,
    remember: bool,
    user_agent: str = "",
    ip_hint: str = "",
    remember_days: int = 30,
    session_hours: int = 12,
) -> tuple[str, str, AuthSession]:
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    lifetime = timedelta(days=remember_days) if remember else timedelta(hours=session_hours)
    item = AuthSession(
        user_id=user_id,
        token_hash=token_digest(token),
        csrf_hash=token_digest(csrf),
        expires_at=now() + lifetime,
        user_agent=user_agent[:300],
        ip_hint=ip_hint[:80],
    )
    with Session(engine) as session:
        session.add(item)
        session.commit()
        session.refresh(item)
    return token, csrf, item


def resolve_session(engine, token: str | None) -> tuple[User, AuthSession] | None:
    if not token:
        return None
    with Session(engine) as session:
        auth_session = session.exec(
            select(AuthSession).where(AuthSession.token_hash == token_digest(token))
        ).first()
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or aware(auth_session.expires_at) <= now()
        ):
            return None
        user = session.get(User, auth_session.user_id)
        if user is None or not user.active:
            return None
        # Avoid a write on every poll. A five-minute precision is sufficient for session UI.
        if aware(auth_session.last_seen_at) < now() - timedelta(minutes=5):
            auth_session.last_seen_at = now()
            session.add(auth_session)
            session.commit()
            session.refresh(user)
            session.refresh(auth_session)
        return user, auth_session


def csrf_valid(auth_session: AuthSession, cookie_value: str | None, header_value: str | None) -> bool:
    return bool(
        cookie_value
        and header_value
        and hmac.compare_digest(cookie_value, header_value)
        and hmac.compare_digest(token_digest(cookie_value), auth_session.csrf_hash)
    )


def revoke_session(engine, token: str | None) -> None:
    if not token:
        return
    with Session(engine) as session:
        item = session.exec(
            select(AuthSession).where(AuthSession.token_hash == token_digest(token))
        ).first()
        if item and item.revoked_at is None:
            item.revoked_at = now()
            session.add(item)
            session.commit()


def revoke_all_user_sessions(engine, user_id: UUID, except_session_id: UUID | None = None) -> int:
    with Session(engine) as session:
        statement = (
            update(AuthSession)
            .where(AuthSession.user_id == user_id, col(AuthSession.revoked_at).is_(None))
            .values(revoked_at=now())
        )
        if except_session_id:
            statement = statement.where(AuthSession.id != except_session_id)
        count = int(session.exec(statement).rowcount)
        session.commit()
        return count


def sweep_auth_records(engine) -> dict[str, int]:
    """Remove expired/revoked credentials; user data and resource grants are untouched."""
    cutoff = now() - timedelta(days=7)
    with Session(engine) as session:
        sessions = int(
            session.exec(
                delete(AuthSession).where(
                    (AuthSession.expires_at < now())
                    | ((col(AuthSession.revoked_at).is_not(None)) & (AuthSession.revoked_at < cutoff))
                )
            ).rowcount
        )
        invitations = int(
            session.exec(
                delete(Invitation).where(
                    (Invitation.expires_at < now())
                    | ((col(Invitation.revoked_at).is_not(None)) & (Invitation.revoked_at < cutoff))
                )
            ).rowcount
        )
        session.commit()
    return {"sessions": sessions, "invitations": invitations}


def create_invitation(
    engine, *, email: str, role: str, invited_by: UUID, lifetime_hours: int = 168
) -> tuple[Invitation, str]:
    normalized = normalize_email(email)
    if role not in {"admin", "member"}:
        raise ValueError("Invitations may grant the admin or member role.")
    token = secrets.token_urlsafe(36)
    item = Invitation(
        email=normalized,
        role=role,
        token_hash=token_digest(token),
        invited_by=invited_by,
        expires_at=now() + timedelta(hours=max(1, min(lifetime_hours, 720))),
    )
    with Session(engine) as session:
        if session.exec(select(User).where(User.email == normalized)).first():
            raise ValueError("That email already belongs to a workspace member.")
        session.add(item)
        session.commit()
        session.refresh(item)
    return item, token


def permission_for(engine, user_id: UUID, resource_type: str, resource_id: str) -> str | None:
    with Session(engine) as session:
        item = session.exec(
            select(ResourceShare).where(
                ResourceShare.recipient_id == user_id,
                ResourceShare.resource_type == resource_type,
                ResourceShare.resource_id == resource_id,
            )
        ).first()
        return item.permission if item else None


def create_share(
    engine,
    *,
    owner_id: UUID,
    recipient_email: str,
    resource_type: str,
    resource_id: str,
    permission: str,
) -> ResourceShare:
    if resource_type not in {"conversation", "job", "estimate"}:
        raise ValueError("Unsupported resource type.")
    if permission not in {"viewer", "editor"}:
        raise ValueError("Permission must be viewer or editor.")
    normalized = normalize_email(recipient_email)
    with Session(engine) as session:
        recipient = session.exec(select(User).where(User.email == normalized)).first()
        if recipient is None or not recipient.active:
            raise ValueError("No active workspace member has that email.")
        if recipient.id == owner_id:
            raise ValueError("You already own this item.")
        existing = session.exec(
            select(ResourceShare).where(
                ResourceShare.resource_type == resource_type,
                ResourceShare.resource_id == resource_id,
                ResourceShare.recipient_id == recipient.id,
            )
        ).first()
        if existing:
            existing.permission = permission
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing
        item = ResourceShare(
            owner_id=owner_id,
            recipient_id=recipient.id,
            resource_type=resource_type,
            resource_id=resource_id,
            permission=permission,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def share_dict(
    engine, item: ResourceShare, people: dict[UUID, User] | None = None
) -> dict[str, Any]:
    """Project one share. Pass ``people`` when rendering a list — see ``list_shares``."""
    if people is not None:
        owner, recipient = people.get(item.owner_id), people.get(item.recipient_id)
    else:
        with Session(engine) as session:
            owner = session.get(User, item.owner_id)
            recipient = session.get(User, item.recipient_id)
    return {
        "id": str(item.id),
        "resource_type": item.resource_type,
        "resource_id": item.resource_id,
        "permission": item.permission,
        "owner": public_user(owner) if owner else None,
        "recipient": public_user(recipient) if recipient else None,
        "created_at": aware(item.created_at).isoformat(),
    }


def list_shares(engine, user_id: UUID, *, incoming: bool) -> list[dict[str, Any]]:
    """Every share for a user, with both parties resolved in a single extra query.

    Projecting each row independently opened two sessions and issued two queries per share,
    so a list of twenty cost forty round trips to render names that mostly repeat.
    """
    with Session(engine) as session:
        column = ResourceShare.recipient_id if incoming else ResourceShare.owner_id
        rows = list(
            session.exec(
                select(ResourceShare)
                .where(column == user_id)
                .order_by(col(ResourceShare.created_at).desc())
            ).all()
        )
        identifiers = {item.owner_id for item in rows} | {item.recipient_id for item in rows}
        people = {
            person.id: person
            for person in session.exec(select(User).where(col(User.id).in_(identifiers))).all()
        } if identifiers else {}
    return [share_dict(engine, item, people) for item in rows]

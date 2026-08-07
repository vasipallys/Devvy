"""Authentication and access-control boundaries for the multi-user workspace."""

import asyncio
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session

from backend import api
from backend.auth import (
    CSRF_COOKIE,
    ArtifactRecord,
    AuthSession,
    Invitation,
    ResourceShare,
    UploadRecord,
    User,
)
from backend.db import Conversation, Message, init_db
from backend.estimate_history import EstimateRecord
from backend.jobs import Job, JobEvent


def clear_identity_data() -> None:
    """Delete in foreign-key order; the shared pytest database is reused by API tests."""
    with Session(api.engine) as session:
        for model in (
            ResourceShare, AuthSession, Invitation, UploadRecord, ArtifactRecord, JobEvent, Job,
            Message, Conversation, EstimateRecord, User,
        ):
            session.exec(delete(model))
        session.commit()


@pytest.fixture
def authenticated_app(monkeypatch):
    init_db()
    clear_identity_data()
    monkeypatch.setattr(api.settings, "auth_enabled", True)
    monkeypatch.setattr(api.settings, "auth_secure_cookies", False)
    yield
    clear_identity_data()


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get(CSRF_COOKIE)}


def register(client: TestClient, email: str, name: str, invite_token: str | None = None):
    return client.post(
        "/api/auth/register",
        json={
            "email": email,
            "display_name": name,
            "password": "Correct horse battery staple!42",
            "invite_token": invite_token,
        },
    )


def test_first_owner_setup_session_and_csrf_boundary(authenticated_app):
    with Session(api.engine) as session:
        legacy = Conversation(title="Conversation from the single-user build")
        session.add(legacy)
        session.commit()
    with TestClient(api.app) as client:
        initial = client.get("/api/auth/session").json()
        assert initial["authenticated"] is False
        assert initial["needs_setup"] is True
        assert client.get("/api/conversations").status_code == 401

        created = register(client, "owner@example.com", "Workspace Owner")
        assert created.status_code == 201
        assert created.json()["user"]["role"] == "owner"
        assert client.cookies.get(CSRF_COOKIE)

        # Authentication alone is insufficient for a write: the readable CSRF cookie must
        # be deliberately repeated as a header by the first-party client.
        assert client.post("/api/conversations").status_code == 403
        conversation = client.post("/api/conversations", headers=csrf(client))
        assert conversation.status_code == 200
        owned = client.get("/api/conversations").json()
        assert len(owned) == 2
        assert {item["title"] for item in owned} >= {"Conversation from the single-user build"}

        signed_out = client.post(
            "/api/auth/logout", headers=csrf(client), json={"active_job_action": "keep"}
        )
        assert signed_out.status_code == 200
        assert client.get("/api/conversations").status_code == 401


def test_users_only_see_owned_data_until_the_owner_shares_it(authenticated_app):
    with TestClient(api.app) as owner:
        owner_state = register(owner, "owner@example.com", "Owner").json()
        owner_id = owner_state["user"]["id"]
        conversation = owner.post("/api/conversations", headers=csrf(owner)).json()
        invitation = owner.post(
            "/api/auth/invitations",
            headers=csrf(owner),
            json={"email": "member@example.com", "role": "member"},
        )
        assert invitation.status_code == 201
        invitation_token = invitation.json()["invite_token"]

    with TestClient(api.app) as member:
        member_state = register(
            member,
            "member@example.com",
            "Team Member",
            invitation_token,
        )
        assert member_state.status_code == 201

        assert member.get("/api/conversations").json() == []
        assert member.get(f"/api/conversations/{conversation['id']}/messages").status_code == 404

    with TestClient(api.app) as owner:
        assert owner.post(
            "/api/auth/login",
            json={"email": "owner@example.com", "password": "Correct horse battery staple!42"},
        ).status_code == 200
        shared = owner.post(
            "/api/access/shares",
            headers=csrf(owner),
            json={
                "resource_type": "conversation",
                "resource_id": conversation["id"],
                "recipient_email": "member@example.com",
                "permission": "editor",
            },
        )
        assert shared.status_code == 201

    with TestClient(api.app) as member:
        assert member.post(
            "/api/auth/login",
            json={"email": "member@example.com", "password": "Correct horse battery staple!42"},
        ).status_code == 200
        assert member.get(f"/api/conversations/{conversation['id']}/messages").status_code == 200
        renamed = member.patch(
            f"/api/conversations/{conversation['id']}",
            headers=csrf(member),
            json={"title": "Shared refinement"},
        )
        assert renamed.status_code == 200
        assert member.delete(
            f"/api/conversations/{conversation['id']}", headers=csrf(member)
        ).status_code == 404, "editor access never transfers destructive ownership"

        # The resource appears in the sharing inbox, not in the member's owned history.
        inbox = member.get("/api/access/shares?incoming=true").json()
        assert inbox[0]["owner"]["id"] == owner_id
        assert member.get("/api/conversations").json() == []


def test_logout_can_cancel_every_active_job_owned_by_the_user(authenticated_app):
    async def slow_handler(_request, _context):
        await asyncio.sleep(30)
        return {"finished": True}

    api.job_runner.register("auth-test", slow_handler)
    with TestClient(api.app) as client:
        state = register(client, "owner@example.com", "Owner").json()
        job = api.job_runner.submit(
            "auth-test", "private work", {}, owner_id=UUID(state["user"]["id"])
        )
        response = client.post(
            "/api/auth/logout", headers=csrf(client), json={"active_job_action": "cancel"}
        )
        assert response.status_code == 200
        assert response.json()["cancelled_jobs"] == 1
        for _ in range(100):
            if api.job_runner.get(job.id)["status"] == "cancelled":
                break
            time.sleep(0.02)
        assert api.job_runner.get(job.id)["status"] == "cancelled"


def test_the_login_limiter_does_not_grow_without_bound():
    """A failed attempt creates a key; only a successful sign-in removes one.

    Spraying distinct addresses would otherwise grow the attempt map for the life of the
    process — the limiter protecting the sign-in endpoint becoming its own resource leak.
    The ceiling must hold even for a burst that stays inside one window.
    """
    from backend.api import LoginLimiter

    limiter = LoginLimiter()
    for index in range(LoginLimiter.MAX_KEYS * 2):
        limiter.allow(f"10.0.0.1:user{index}@example.com")

    assert len(limiter._attempts) <= LoginLimiter.MAX_KEYS + 1


def test_the_login_limiter_still_blocks_after_a_sweep():
    """Sweeping must not become a way to reset an in-progress attack."""
    from backend.api import LoginLimiter, settings

    limiter = LoginLimiter()
    key = "10.0.0.1:victim@example.com"
    for _ in range(settings.auth_login_attempts):
        assert limiter.allow(key) is True
    assert limiter.allow(key) is False

    # Flood the map past its ceiling. Eviction is least-recently-active first, so an
    # attack in progress must not be able to clear its own lockout by making noise.
    for index in range(LoginLimiter.MAX_KEYS * 2):
        limiter.allow(f"10.0.0.2:other{index}@example.com")
        limiter.allow(key)
    assert limiter.allow(key) is False, "an active lockout survives eviction"

from datetime import UTC, datetime, timedelta

import pyotp
import pytest

from app.config import get_settings
from app.db.models import AuditLog, AuthSession, Role
from app.db.session import get_db
from app.main import create_app
from app.storage import get_storage
from tests.conftest import PASSWORD, client_for, make_user, signed_in


def login(client, user, *, password=PASSWORD, code=None):
    return client.post(
        "/auth/login",
        json={
            "officer_id": user.officer_id,
            "password": password,
            "totp": code or pyotp.TOTP(user.totp_secret).now(),
        },
    )


def test_login_needs_password_and_totp(anon, db):
    user = make_user(db, "OFF-9", Role.OFFICER)
    assert login(anon, user, password="wrong-password-here").status_code == 401
    assert login(anon, user, code="000000").status_code == 401
    good = login(anon, user)
    assert good.status_code == 200
    cookie = good.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie


def test_session_token_is_stored_hashed(anon, db):
    user = make_user(db, "OFF-9", Role.OFFICER)
    login(anon, user)
    token = anon.cookies[get_settings().session_cookie]
    assert db.get(AuthSession, token) is None


def test_totp_code_cannot_be_replayed(anon, db):
    user = make_user(db, "OFF-9", Role.OFFICER)
    code = pyotp.TOTP(user.totp_secret).now()
    assert login(anon, user, code=code).status_code == 200
    assert login(anon, user, code=code).status_code == 401


def test_lockout_blocks_even_the_right_password(anon, db):
    user = make_user(db, "OFF-9", Role.OFFICER)
    for _ in range(get_settings().login_max_failures):
        assert login(anon, user, password="wrong-password-here").status_code == 401
    assert user.locked_until is not None
    assert login(anon, user).status_code == 401


def test_failed_login_is_audited(anon, db):
    user = make_user(db, "OFF-9", Role.OFFICER)
    login(anon, user, password="wrong-password-here")
    row = db.query(AuditLog).filter_by(action="LOGIN_FAILED").one()
    assert row.actor_officer_id == "OFF-9"


def test_unknown_officer_gets_the_same_answer(anon, db):
    user = make_user(db, "OFF-9", Role.OFFICER)
    known = login(anon, user, password="wrong-password-here")
    unknown = anon.post(
        "/auth/login", json={"officer_id": "NOBODY", "password": "x" * 12, "totp": "123456"}
    )
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())


def test_anonymous_is_refused_everywhere(anon):
    for method, path in [
        ("get", "/auth/me"),
        ("get", "/search"),
        ("get", "/records"),
        ("get", "/audit"),
        ("get", "/admin/users"),
    ]:
        assert getattr(anon, method)(path).status_code == 401


def test_mutation_without_client_header_is_refused(officer):
    del officer.headers["x-rkl-client"]
    assert officer.post("/auth/logout").status_code == 403


def test_idle_session_expires(officer, db):
    session = db.query(AuthSession).one()
    session.last_seen_at = datetime.now(UTC) - timedelta(hours=2)
    db.commit()
    assert officer.get("/auth/me").status_code == 401
    assert db.query(AuthSession).count() == 0


def test_disabled_user_loses_access(officer, officer_user, db):
    officer_user.active = False
    db.commit()
    assert officer.get("/auth/me").status_code == 401


def test_temporary_password_must_be_changed_first(app, db):
    user = make_user(db, "NEW-1", Role.OFFICER, must_change=True)
    client = signed_in(app, db, user)
    blocked = client.get("/records")
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "PASSWORD_CHANGE_REQUIRED"
    assert client.get("/auth/me").status_code == 200
    changed = client.post(
        "/auth/password", json={"current": PASSWORD, "new": "a-much-better-password"}
    )
    assert changed.status_code == 204
    assert client.get("/records").status_code == 200


@pytest.fixture
def mock_client(db, storage, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_login", True)
    application = create_app()
    application.dependency_overrides[get_db] = lambda: db
    application.dependency_overrides[get_storage] = lambda: storage
    return client_for(application)


def test_mock_login_routes_do_not_exist_by_default(anon):
    assert anon.get("/auth/dev-accounts").status_code == 404
    assert anon.post("/auth/dev-login", json={"officer_id": "OFF-9"}).status_code == 404


def test_mock_login_routes_do_not_exist_in_production(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_login", True)
    monkeypatch.setattr(get_settings(), "env", "production")
    assert client_for(create_app()).get("/auth/dev-accounts").status_code == 404


def test_mock_login_lists_active_accounts_and_signs_in(mock_client, db):
    make_user(db, "OFF-9", Role.OFFICER)
    make_user(db, "ADM-9", Role.ADMIN)
    gone = make_user(db, "OFF-8", Role.OFFICER)
    gone.active = False
    db.commit()
    listed = mock_client.get("/auth/dev-accounts").json()
    assert [a["officer_id"] for a in listed] == ["ADM-9", "OFF-9"]
    assert "totp_secret" not in str(listed) and "password" not in str(listed)
    assert mock_client.post("/auth/dev-login", json={"officer_id": "ADM-9"}).status_code == 200
    assert mock_client.get("/auth/me").json()["role"] == "ADMIN"


def test_mock_login_is_audited_as_mock(mock_client, db):
    make_user(db, "OFF-9", Role.OFFICER)
    mock_client.post("/auth/dev-login", json={"officer_id": "OFF-9"})
    assert db.query(AuditLog).filter_by(action="LOGIN").one().detail == {"mock": True}


def test_mock_login_refuses_disabled_accounts(mock_client, db):
    user = make_user(db, "OFF-9", Role.OFFICER)
    user.active = False
    db.commit()
    assert mock_client.post("/auth/dev-login", json={"officer_id": "OFF-9"}).status_code == 404

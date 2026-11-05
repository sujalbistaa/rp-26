"""Roles are enforced in the API. Each row is a route a lower role must not reach."""

import pytest

from app.db.models import Role
from tests.conftest import RED, enroll, make_user, probe, signed_in
from tests.fakes import block_photo

ANALYST_ROUTES = [
    ("post", "/enroll"),
    ("post", "/enroll/bulk"),
    ("get", "/enroll/batches/00000000-0000-0000-0000-000000000000"),
    ("patch", "/records/00000000-0000-0000-0000-000000000000"),
    ("post", "/records/00000000-0000-0000-0000-000000000000/cases"),
    ("post", "/records/00000000-0000-0000-0000-000000000000/notes"),
]
ADMIN_ROUTES = [
    ("get", "/audit"),
    ("get", "/audit/export.csv"),
    ("get", "/admin/users"),
    ("post", "/admin/users"),
    ("patch", "/admin/users/00000000-0000-0000-0000-000000000000"),
    ("get", "/admin/calibrations"),
    ("post", "/admin/calibrations/1/activate"),
    ("patch", "/admin/calibrations/1/floor"),
]


def call(client, method, path):
    if method == "get":
        return client.get(path)
    return getattr(client, method)(path, json={})


@pytest.mark.parametrize(("method", "path"), ANALYST_ROUTES + ADMIN_ROUTES)
def test_officer_is_refused(officer, method, path):
    assert call(officer, method, path).status_code == 403


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_analyst_is_refused_admin_routes(analyst, method, path):
    assert call(analyst, method, path).status_code == 403


def test_analyst_can_enroll_and_edit(analyst):
    person = enroll(analyst, "Test Subject", block_photo([(20, 20, 180)]))
    edited = analyst.patch(f"/records/{person['person_id']}", json={"gender": "M"})
    assert edited.status_code == 200


def test_officer_cannot_read_another_officers_search(app, db, officer, calibration):
    created = probe(officer, block_photo([RED])).json()
    other = signed_in(app, db, make_user(db, "OFF-2", Role.OFFICER))
    assert other.get(f"/search/{created['id']}").status_code == 403
    assert other.get(f"/search/{created['id']}/probe").status_code == 403
    assert other.post(f"/search/{created['id']}/run", json={}).status_code == 403


def test_admin_creates_user_and_secrets_show_once(admin):
    created = admin.post(
        "/admin/users", json={"officer_id": "OFF-7", "name": "New Officer", "role": "OFFICER"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["temporary_password"] and body["totp_secret"]
    assert body["user"]["must_change_password"] is True
    listed = admin.get("/admin/users").text
    assert body["temporary_password"] not in listed and body["totp_secret"] not in listed


def test_admin_cannot_demote_themselves(admin):
    me = admin.get("/auth/me").json()
    response = admin.patch(f"/admin/users/{me['id']}", json={"role": "OFFICER"})
    assert response.status_code == 400

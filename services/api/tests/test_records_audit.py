import csv
import io

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models import AuditLog, Role
from app.routers.audit import csv_safe
from tests.conftest import RED, enroll, make_user, search, signed_in
from tests.fakes import block_photo

SECRETS = {"address": "12 Test Lane", "phone": "9000000000", "id_number": "XX-0000"}


@pytest.fixture
def person(analyst, calibration):
    return enroll(analyst, "Subject Red", block_photo([RED]), **SECRETS)


def test_sensitive_fields_stay_out_of_the_record_payload(officer, person):
    body = officer.get(f"/records/{person['person_id']}")
    assert body.status_code == 200
    for value in SECRETS.values():
        assert value not in body.text
    assert body.json()["has_address"] is True


def test_sensitive_fields_are_ciphertext_in_the_database(officer, person, db):
    row = db.execute(
        text("SELECT address, phone, id_number FROM persons WHERE id = :id"),
        {"id": person["person_id"]},
    ).one()
    for stored, value in zip(row, SECRETS.values(), strict=True):
        assert value.encode() not in bytes(stored)


@pytest.mark.parametrize("field", SECRETS)
def test_reveal_returns_the_value_and_writes_an_audit_row(officer, person, db, field):
    response = officer.post(f"/records/{person['person_id']}/reveal", json={"field": field})
    assert response.json()["value"] == SECRETS[field]
    row = db.query(AuditLog).filter_by(action="REVEAL").one()
    assert row.actor_officer_id == "OFF-1"
    assert row.detail["field"] == field
    assert SECRETS[field] not in str(row.detail)


def test_reveal_rejects_other_fields(officer, person):
    response = officer.post(
        f"/records/{person['person_id']}/reveal", json={"field": "password_hash"}
    )
    assert response.status_code == 422


def test_reveal_from_a_search_carries_the_case_number(officer, person, db):
    result = search(officer, block_photo([RED], seed=2))
    officer.post(
        f"/records/{person['person_id']}/reveal",
        json={"field": "phone", "search_id": result["id"]},
    )
    assert db.query(AuditLog).filter_by(action="REVEAL").one().case_number == "214/26"


def test_another_officers_search_cannot_label_your_audit_row(app, db, officer, person):
    result = search(officer, block_photo([RED], seed=2))
    other = signed_in(app, db, make_user(db, "OFF-2", Role.OFFICER))
    other.post(
        f"/records/{person['person_id']}/reveal",
        json={"field": "phone", "search_id": result["id"]},
    )
    other.get(f"/records/{person['person_id']}", params={"search_id": result["id"]})
    rows = db.query(AuditLog).filter(AuditLog.actor_officer_id == "OFF-2").all()
    assert rows and all(r.case_number is None for r in rows)


def test_record_view_writes_an_audit_row(officer, person, db):
    officer.get(f"/records/{person['person_id']}")
    assert db.query(AuditLog).filter_by(action="RECORD_VIEW").count() == 1


def test_edit_audits_field_names_not_values(analyst, person, db):
    analyst.patch(f"/records/{person['person_id']}", json={"phone": "9111111111"})
    row = db.query(AuditLog).filter_by(action="RECORD_EDIT").one()
    assert row.detail["fields"] == ["phone"]
    assert "9111111111" not in str(row.detail)


def test_record_search_treats_wildcards_literally(officer, person):
    assert officer.get("/records", params={"q": "%"}).json()["total"] == 0
    assert officer.get("/records", params={"q": "subject"}).json()["total"] == 1


def test_record_list_carries_a_cover_image(officer, person):
    record = officer.get(f"/records/{person['person_id']}").json()
    row = officer.get("/records").json()["items"][0]
    assert row["cover_image_id"] == record["images"][0]["id"]


def test_log_tab_lists_access_without_revealed_values(officer, person):
    officer.post(f"/records/{person['person_id']}/reveal", json={"field": "phone"})
    log = officer.get(f"/records/{person['person_id']}/log").json()
    assert log[0]["action"] == "REVEAL"
    assert SECRETS["phone"] not in str(log)


def test_images_need_a_session(anon, officer, person):
    record = officer.get(f"/records/{person['person_id']}").json()
    url = f"/records/images/{record['images'][0]['id']}"
    assert officer.get(url).status_code == 200
    assert anon.get(url).status_code == 401


def test_notes_and_cases(analyst, person):
    base = f"/records/{person['person_id']}"
    assert analyst.post(f"{base}/notes", json={"body": "Seen near the station."}).status_code == 201
    assert analyst.post(f"{base}/cases", json={"case_number": "99/26"}).status_code == 201
    record = analyst.get(base).json()
    assert record["notes"][0]["author"] == "ANA-1"
    assert record["cases"][0]["case_number"] == "99/26"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'X'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
def test_audit_log_is_append_only(officer, person, db, statement):
    officer.get(f"/records/{person['person_id']}")
    nested = db.begin_nested()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text(statement))
    nested.rollback()


def test_audit_filter_and_export_is_itself_audited(admin, officer, person, db):
    officer.get(f"/records/{person['person_id']}")
    listed = admin.get("/audit", params={"action": "RECORD_VIEW"}).json()
    assert listed["total"] == 1
    assert listed["items"][0]["actor_officer_id"] == "OFF-1"

    export = admin.get("/audit/export.csv", params={"action": "RECORD_VIEW"})
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert len(rows) == 1 and rows[0]["action"] == "RECORD_VIEW"
    assert db.query(AuditLog).filter_by(action="EXPORT").count() == 1


@pytest.mark.parametrize("value", ["=HYPERLINK(1)", "+1", "-1", "@x"])
def test_csv_export_neutralises_formulas(value):
    assert csv_safe(value).startswith("'")


def test_activation_and_floor_changes_are_audited(admin, calibration, db):
    version = calibration.version
    assert admin.post(f"/admin/calibrations/{version}/activate").status_code == 200
    assert admin.patch(
        f"/admin/calibrations/{version}/floor", json={"floor": 0.8}
    ).status_code == 200
    rows = db.query(AuditLog).filter_by(action="THRESHOLD_CHANGE").all()
    assert len(rows) == 2 and all(r.calibration_version == version for r in rows)


def test_floor_can_be_raised_but_not_lowered(admin, calibration):
    url = f"/admin/calibrations/{calibration.version}/floor"
    assert admin.patch(url, json={"floor": 0.1}).status_code == 422
    raised = admin.patch(url, json={"floor": 0.8}).json()
    assert raised["floor"] == pytest.approx(0.8)
    assert raised["floor_calibrated"] == pytest.approx(0.58)
    assert admin.patch(url, json={"floor": None}).json()["floor"] == pytest.approx(0.58)

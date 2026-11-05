import io
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from app import jobs
from app.config import get_settings
from app.db.models import (
    AuditLog,
    BatchStatus,
    EnrollBatch,
    ImageSource,
    Person,
    PersonImage,
    Search,
)
from app.jobs import (
    BadBatch,
    check_archive,
    parse_manifest,
    purge_expired_probes,
    reembed_gallery,
    run_bulk,
)
from tests.conftest import GREEN, RED, enroll, probe, search
from tests.fakes import block_photo, fake_ml


def post(client, photo, **fields):
    return client.post(
        "/enroll",
        data={"full_name": "Subject", **fields},
        files=[("files", ("a.png", photo, "image/png"))],
    ).json()


def test_enroll_assigns_a_record_id_and_reports_quality(analyst):
    body = enroll(analyst, "Subject", block_photo([RED]))
    year = datetime.now(UTC).year
    assert body["record_id"] == f"RKL-{year}-000001"
    item = body["report"][0]
    assert item["status"] == "ENROLLED" and item["face_px"] == 200
    assert item["blur"] > 0 and item["low_quality"] is False


def test_record_ids_increment(analyst):
    enroll(analyst, "One", block_photo([RED]))
    second = enroll(analyst, "Two", block_photo([GREEN]))
    assert second["record_id"].endswith("000002")


def test_multi_face_photo_is_rejected(analyst):
    body = post(analyst, block_photo([RED, GREEN]))
    assert body["report"][0]["status"] == "REJECTED"
    assert "2 faces" in body["report"][0]["reason"]


def test_no_usable_photo_leaves_no_record(analyst, db):
    body = post(analyst, block_photo([]))
    assert body["record_id"] is None
    assert db.query(Person).count() == 0


def test_exact_copy_is_skipped(analyst):
    photo = block_photo([RED])
    first = enroll(analyst, "One", photo)
    second = analyst.post(
        "/enroll",
        data={"record_id": first["record_id"]},
        files=[("files", ("copy.png", photo, "image/png"))],
    ).json()
    assert second["report"][0]["status"] == "SKIPPED"
    assert second["report"][0]["duplicates"][0]["record_id"] == first["record_id"]


def test_same_face_on_another_record_is_listed(analyst, calibration, db):
    calibration.strong_cos = 0.5
    db.commit()
    first = enroll(analyst, "One", block_photo([RED], seed=1))
    second = enroll(analyst, "Two", block_photo([RED], seed=2))
    assert second["report"][0]["duplicates"][0]["record_id"] == first["record_id"]


def test_enroll_is_audited(analyst, db):
    enroll(analyst, "One", block_photo([RED]))
    assert db.query(AuditLog).filter_by(action="ENROLL").one().results_returned == 1


def test_stored_originals_are_encrypted(analyst, backing):
    photo = block_photo([RED])
    enroll(analyst, "One", photo)
    assert photo not in backing.objects.values()
    assert all(blob.startswith(b"RKL1") for blob in backing.objects.values())


def make_zip(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


CSV = (
    "group,full_name,image_filename,source,phone\n"
    "a,Subject A,a1.png,CCTV,9000000001\n"
    "a,Subject A,a2.png,,\n"
    "b,Subject B,missing.png,,\n"
    "c,Subject C,blank.png,,\n"
)


def run_batch(db, storage, user):
    storage.put("m.csv", CSV.encode())
    storage.put(
        "z.zip",
        make_zip(
            {
                "a1.png": block_photo([RED], seed=1),
                "a2.png": block_photo([RED], seed=2),
                "blank.png": block_photo([]),
            }
        ),
    )
    batch = EnrollBatch(
        created_by=user.id,
        csv_key="m.csv",
        zip_key="z.zip",
        default_source=ImageSource.MUGSHOT,
        total=4,
    )
    db.add(batch)
    db.commit()
    run_bulk(db, batch, fake_ml, storage, get_settings())
    return batch


def test_bulk_groups_people_and_reports_every_row(db, storage, officer_user):
    batch = run_batch(db, storage, officer_user)
    assert batch.status == BatchStatus.DONE
    assert [r["status"] for r in batch.report] == [
        "ENROLLED",
        "ENROLLED",
        "REJECTED",
        "REJECTED",
    ]
    people = db.query(Person).all()
    assert len(people) == 1 and len(people[0].images) == 2
    assert people[0].phone == "9000000001"
    assert people[0].images[0].source == ImageSource.CCTV


def test_manifest_needs_required_columns():
    with pytest.raises(BadBatch, match="missing columns"):
        parse_manifest(b"full_name\nX\n")


def test_oversize_zip_member_is_refused_before_a_worker_sees_it():
    data = make_zip({"bomb.bin": b"\0" * (get_settings().max_upload_bytes + 1)})
    with pytest.raises(BadBatch, match="over 15 MB"):
        check_archive(data, get_settings())


def test_unreadable_zip_is_refused():
    with pytest.raises(BadBatch):
        check_archive(b"not a zip", get_settings())


def test_bulk_endpoint_queues_a_job(analyst, monkeypatch):
    queued = []
    monkeypatch.setattr(jobs, "enqueue_bulk", queued.append)
    response = analyst.post(
        "/enroll/bulk",
        files={
            "manifest": ("m.csv", CSV.encode(), "text/csv"),
            "archive": ("z.zip", make_zip({"a1.png": b"x"}), "application/zip"),
        },
    )
    assert response.status_code == 202
    assert len(queued) == 1


def aged(db, search_id, days):
    row = db.get(Search, search_id)
    row.created_at = datetime.now(UTC) - timedelta(days=days)
    db.commit()
    return row


def test_old_probe_is_purged_and_audited(officer, calibration, db, storage, backing):
    created = probe(officer, block_photo([RED])).json()
    row = aged(db, created["id"], 91)
    assert purge_expired_probes(db, storage, get_settings()) == 1
    assert row.purged_at is not None
    assert row.probe_key not in backing.objects
    audit_row = db.query(AuditLog).filter_by(action="PROBE_PURGE").one()
    assert audit_row.probe_hash == row.probe_sha256
    assert officer.get(f"/search/{created['id']}/probe").status_code == 410
    assert officer.get("/search").json()[0]["purged"] is True


def test_recent_probe_is_kept(officer, calibration, db, storage):
    created = probe(officer, block_photo([RED])).json()
    aged(db, created["id"], 89)
    assert purge_expired_probes(db, storage, get_settings()) == 0


def test_probe_attached_to_a_case_is_kept(officer, calibration, db, storage):
    result = search(officer, block_photo([RED]))
    assert officer.post(f"/search/{result['id']}/attach").status_code == 204
    aged(db, result["id"], 400)
    assert purge_expired_probes(db, storage, get_settings()) == 0
    assert db.query(AuditLog).filter_by(action="CASE_ATTACH").count() == 1


def test_retention_is_configurable(officer, calibration, db, storage, monkeypatch):
    created = probe(officer, block_photo([RED])).json()
    aged(db, created["id"], 10)
    monkeypatch.setattr(get_settings(), "probe_retention_days", 7)
    assert purge_expired_probes(db, storage, get_settings()) == 1


def test_reembed_fills_the_other_column(analyst, db, storage):
    enroll(analyst, "One", block_photo([RED]))
    done = reembed_gallery(db, fake_ml, storage, get_settings(), "embedding_v2")
    assert done == 1
    assert db.query(PersonImage).one().embedding_v2 is not None



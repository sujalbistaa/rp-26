from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db.models import AuditLog, Calibration, Person, PersonImage, SearchResult
from app.routers.search import nearest_images
from app.schemas import LEAD_NOTICE
from tests.conftest import AMBER, BLUE, GREEN, RED, enroll, probe, search
from tests.fakes import block_photo


def shade(colour, by):
    return tuple(c + by for c in colour)


@pytest.fixture
def gallery(analyst, calibration):
    red = enroll(
        analyst,
        "Subject Red",
        block_photo([RED], seed=1),
        block_photo([shade(RED, 3)], seed=2),
        block_photo([shade(RED, 6)], seed=3),
    )
    green = enroll(analyst, "Subject Green", block_photo([GREEN], seed=4))
    return {"red": red, "green": green}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"reason": "Chain snatching, Sector 6"}, None),
        ({"case_number": "214/26"}, None),
        ({"case_number": "  ", "reason": "Chain snatching, Sector 6"}, "case number"),
        ({"case_number": "214/26", "reason": "short"}, "reason"),
    ],
)
def test_no_case_number_no_search(officer, calibration, fields, message):
    response = officer.post(
        "/search/probe",
        data=fields,
        files={"file": ("p.png", block_photo([RED]), "image/png")},
    )
    assert response.status_code == 422
    if message:
        assert message in response.json()["detail"]


def test_no_face(officer, calibration):
    created = probe(officer, block_photo([]))
    assert created.status_code == 201
    assert created.json()["status"] == "NO_FACE"
    run = officer.post(f"/search/{created.json()['id']}/run", json={})
    assert run.status_code == 422
    assert run.json()["detail"] == "Couldn't find a face in this photo."


def test_many_faces_are_never_auto_picked(officer, gallery):
    created = probe(officer, block_photo([GREEN, RED]))
    assert len(created.json()["faces"]) == 2
    url = f"/search/{created.json()['id']}/run"
    refused = officer.post(url, json={})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Pick a face first."
    picked = officer.post(url, json={"face_index": 1})
    assert picked.status_code == 200
    assert picked.json()["people"][0]["person_id"] == gallery["red"]["person_id"]


def test_face_index_out_of_range(officer, gallery):
    created = probe(officer, block_photo([GREEN]))
    run = officer.post(f"/search/{created.json()['id']}/run", json={"face_index": 5})
    assert run.status_code == 422


def test_returns_every_matching_image_grouped_by_person(officer, gallery):
    result = search(officer, block_photo([RED], seed=9))
    assert [p["person_id"] for p in result["people"]] == [gallery["red"]["person_id"]]
    person = result["people"][0]
    assert person["rank"] == 1
    assert person["images_matched"] == 3 and person["images_total"] == 3
    scores = [i["confidence"] for i in person["images"]]
    assert scores == sorted(scores, reverse=True)
    assert person["confidence"] == scores[0]


def test_person_score_is_their_best_image_and_ranks_people(officer, analyst, gallery):
    near = enroll(analyst, "Lookalike", block_photo([shade(RED, 40)], seed=5))
    result = search(officer, block_photo([RED], seed=9))
    confidences = [p["confidence"] for p in result["people"]]
    assert confidences == sorted(confidences, reverse=True)
    assert near["person_id"] != result["people"][0]["person_id"]


def test_raw_cosine_never_reaches_the_client(officer, gallery, db):
    result = search(officer, block_photo([shade(RED, 9)], seed=9))
    assert "cosine" not in str(result)
    row = db.query(SearchResult).first()
    assert row.confidence != row.cosine


def test_bands_come_from_the_calibration_row(officer, gallery, calibration, db):
    calibration.strong_cos = 0.99
    calibration.possible_cos = 0.3
    db.commit()
    result = search(officer, block_photo([shade(RED, 9)], seed=9))
    assert result["people"][0]["band"] == "POSSIBLE"


def test_nothing_above_the_floor_is_an_explicit_empty_result(officer, gallery):
    result = search(officer, block_photo([BLUE]))
    assert result["status"] == "COMPLETE"
    assert result["people"] == []
    assert result["floor"] == pytest.approx(0.58)


def test_never_pads_results(officer, gallery, db):
    search(officer, block_photo([BLUE]))
    assert db.query(SearchResult).count() == 0


def test_notice_is_on_every_results_payload(officer, gallery):
    assert search(officer, block_photo([RED]))["notice"] == LEAD_NOTICE
    assert search(officer, block_photo([BLUE]))["notice"] == LEAD_NOTICE


def test_low_quality_probe_can_search_but_is_flagged(officer, gallery):
    photo = block_photo([RED], textured=False)
    created = probe(officer, photo).json()
    assert created["faces"][0]["quality"]["too_blurry"] is True
    result = officer.post(f"/search/{created['id']}/run", json={}).json()
    assert result["low_quality"] is True
    assert result["people"]


def test_small_face_is_flagged(officer, calibration):
    created = probe(officer, block_photo([RED], size=60)).json()
    assert created["faces"][0]["quality"]["too_small"] is True


def test_search_audit_row_has_every_required_field(officer, officer_user, gallery, calibration, db):
    result = search(officer, block_photo([RED], seed=9))
    row = db.query(AuditLog).filter_by(action="SEARCH").one()
    assert row.actor_id == officer_user.id
    assert row.at is not None
    assert row.ip is not None
    assert row.case_number == "214/26"
    assert len(row.probe_hash) == 64
    assert row.results_returned == sum(p["images_matched"] for p in result["people"])
    assert row.calibration_version == calibration.version


def test_every_result_keeps_its_calibration_version(officer, gallery, calibration, db):
    search(officer, block_photo([RED], seed=9))
    versions = {r.calibration_version for r in db.query(SearchResult)}
    assert versions == {calibration.version}


def test_old_results_survive_a_new_calibration(officer, admin, gallery, calibration, db):
    first = search(officer, block_photo([RED], seed=9))
    newer = Calibration(
        embedder_version="fake-v1",
        cos_knots=[-1.0, 1.0],
        conf_knots=[0.0, 1.0],
        floor_cos=0.95,
        possible_cos=0.97,
        strong_cos=0.99,
        n_genuine=1,
        n_impostor=1,
    )
    db.add(newer)
    db.commit()
    assert admin.post(f"/admin/calibrations/{newer.version}/activate").status_code == 200
    again = officer.get(f"/search/{first['id']}").json()
    assert again["calibration_version"] == calibration.version
    assert again["people"] == first["people"]


def test_search_refuses_without_active_calibration(officer):
    created = probe(officer, block_photo([RED])).json()
    run = officer.post(f"/search/{created['id']}/run", json={})
    assert run.status_code == 409


def test_search_refuses_calibration_from_another_model(officer, calibration, db):
    calibration.embedder_version = "some-other-model"
    db.commit()
    created = probe(officer, block_photo([RED])).json()
    assert officer.post(f"/search/{created['id']}/run", json={}).status_code == 409


def test_production_refuses_synthetic_calibration(officer, calibration, db, monkeypatch):
    calibration.synthetic = True
    db.commit()
    created = probe(officer, block_photo([RED])).json()
    monkeypatch.setattr(get_settings(), "env", "production")
    assert officer.post(f"/search/{created['id']}/run", json={}).status_code == 409


def test_search_runs_once(officer, gallery):
    created = probe(officer, block_photo([RED])).json()
    url = f"/search/{created['id']}/run"
    assert officer.post(url, json={}).status_code == 200
    assert officer.post(url, json={}).status_code == 409


def test_bad_uploads_are_refused(officer, calibration):
    fake = officer.post(
        "/search/probe",
        data={"case_number": "214/26", "reason": "Chain snatching, Sector 6"},
        files={"file": ("p.jpg", b"not an image at all", "image/jpeg")},
    )
    assert fake.status_code == 415


def test_oversize_upload_is_refused(officer, calibration):
    big = b"\xff" * (get_settings().max_upload_bytes + 1)
    response = officer.post(
        "/search/probe",
        data={"case_number": "214/26", "reason": "Chain snatching, Sector 6"},
        files={"file": ("p.jpg", big, "image/jpeg")},
    )
    assert response.status_code == 413


def test_top_k_is_reachable_through_the_hnsw_index(db):
    """ef_search=100 alone caps an HNSW scan near 100 rows. Iterative scan must fill 200."""
    rng = np.random.default_rng(1)
    person = Person(id=uuid4(), record_id="RKL-2026-999999", full_name="Bulk")
    db.add(person)
    db.flush()
    vectors = rng.standard_normal((400, 512)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    for i, vector in enumerate(vectors):
        db.add(
            PersonImage(
                person_id=person.id,
                storage_key=f"k{i}",
                sha256=f"{i:064d}",
                source="OTHER",
                width=1,
                height=1,
                face_box=[0.0] * 4,
                landmarks=[0.0] * 10,
                embedder_version="fake-v1",
                embedding_v1=vector,
            )
        )
    db.flush()
    db.execute(text("SET LOCAL enable_seqscan = off"))
    hits = nearest_images(db, vectors[0], get_settings())
    assert len(hits) == get_settings().search_top_k
    cosines = [c for _, _, c in hits]
    # Row count and order only. Rolled-back test runs leave dead index entries that lower
    # recall until VACUUM, so an exact-match assertion here would be flaky.
    assert cosines == sorted(cosines, reverse=True)


def test_search_query_uses_the_hnsw_index(db):
    db.execute(text("SET LOCAL enable_seqscan = off"))
    plan = db.execute(
        text(
            "EXPLAIN SELECT id FROM person_images WHERE embedding_v1 IS NOT NULL "
            "ORDER BY embedding_v1 <=> (SELECT array_fill(0.1::real, ARRAY[512])::vector) LIMIT 200"
        )
    ).scalars()
    assert "ix_person_images_embedding_v1_hnsw" in "\n".join(plan)


def test_recent_searches_only_lists_your_own(officer, analyst, gallery):
    search(officer, block_photo([RED]))
    rows = officer.get("/search").json()
    assert len(rows) == 1 and rows[0]["people"] == 1
    assert analyst.get("/search").json() == []


def test_amber_is_a_separate_identity(officer, analyst, gallery):
    enroll(analyst, "Subject Amber", block_photo([AMBER], seed=7))
    result = search(officer, block_photo([AMBER], seed=8))
    assert len(result["people"]) == 1

"""Fill a dev database with synthetic records. Refuses to run in production.

    uv run python scripts/seed.py

Run scripts/make_fixtures.py first. Names, addresses and numbers are invented. The last
--heldout identities stay out of the gallery so a demo can show NO MATCH.
"""

import argparse
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyotp

from _paths import ROOT

from sqlalchemy import select  # noqa: E402

from app import security  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import ImageSource, Note, Person, PersonCase, PersonImage, Role, User  # noqa: E402
from app.db.session import new_session  # noqa: E402
from app.enrollment import enroll_image, next_record_id  # noqa: E402
from app.pipeline import get_ml  # noqa: E402
from app.storage import get_storage  # noqa: E402

FIXTURES = ROOT / "data" / "fixtures" / "synthetic"
DEV_PASSWORD = "rourkela-dev-only"  # noqa: S105
# Fixed so a dev can add them to an authenticator once. Dev databases only.
DEV_USERS = [
    ("OFF-1001", "Demo Officer", Role.OFFICER, "JBSWY3DPEHPK3PXP"),
    ("ANA-2001", "Demo Analyst", Role.ANALYST, "KRSXG5DSNFXGOIDB"),
    ("ADM-3001", "Demo Admin", Role.ADMIN, "MFRGGZDFMZTWQ2LK"),
]
FIRST = ["Test", "Sample", "Demo", "Fixture", "Dummy", "Placeholder", "Mock", "Trial"]
LAST = ["Subject", "Record", "Person", "Entry", "Case", "File", "Profile", "Specimen"]
# Everything else in a fixture folder stays out of the gallery, for use as demo probes.
GALLERY = {
    "mugshot_0": ImageSource.MUGSHOT,
    "phone_0": ImageSource.PHONE,
    "sidelit_0": ImageSource.PHONE,
    "cctv_0": ImageSource.CCTV,
}
SECTIONS = ["BNS 303(2)", "BNS 304", "BNS 309(4)", "BNS 331(4)", "BNS 305"]
STATIONS = ["PS Alpha", "PS Bravo", "PS Charlie", "PS Delta"]


def seed_users(db) -> dict[str, User]:
    users = {}
    for officer_id, name, role, secret in DEV_USERS:
        user = db.scalar(select(User).where(User.officer_id == officer_id))
        if user is None:
            user = User(
                officer_id=officer_id,
                name=name,
                role=role,
                password_hash=security.hash_password(DEV_PASSWORD),
                must_change_password=False,
                totp_secret=secret,
            )
            db.add(user)
        users[officer_id] = user
    db.commit()
    return users


def seed_person(db, folder: Path, index: int, analyst: User, rng: random.Random, ml, storage):
    settings = get_settings()
    year = rng.choice([2019, 2021, 2023, 2024, 2025, 2026])
    person = Person(
        record_id=next_record_id(db, year),
        full_name=f"{rng.choice(FIRST)} {rng.choice(LAST)} {index:03d}",
        gender=rng.choice(["M", "F"]),
        birth_year=rng.randint(1965, 2004),
        address=f"Fixture Block {rng.randint(1, 40)}, Test Colony",
        phone=f"90000{rng.randint(10000, 99999)}",
        id_number=f"TEST-{rng.randint(100000, 999999)}",
        created_by=analyst.id,
    )
    db.add(person)
    db.flush()

    enrolled = 0
    for stem, source in GALLERY.items():
        photo = folder / f"{stem}.jpg"
        report = enroll_image(
            db,
            data=photo.read_bytes(),
            filename=photo.name,
            person=person,
            source=source,
            user=analyst,
            ml=ml,
            storage=storage,
            settings=settings,
        )
        enrolled += report.status == "ENROLLED"
    if enrolled == 0:
        db.rollback()
        return

    age = timedelta(days=rng.randint(30, 1800))
    for image in db.scalars(select(PersonImage).where(PersonImage.person_id == person.id)):
        image.enrolled_at = datetime.now(UTC) - age - timedelta(days=rng.randint(0, 60))
    for _ in range(rng.randint(1, 3)):
        db.add(
            PersonCase(
                person_id=person.id,
                case_number=f"{rng.randint(1, 480)}/{str(year)[2:]}",
                section=rng.choice(SECTIONS),
                station=rng.choice(STATIONS),
                status=rng.choice(["OPEN", "CHARGESHEETED", "CLOSED"]),
            )
        )
    if rng.random() < 0.4:
        db.add(
            Note(
                person_id=person.id,
                author_id=analyst.id,
                body="Synthetic record. Generated for development.",
            )
        )
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heldout", type=int, default=40)
    args = parser.parse_args()
    if get_settings().env == "production":
        raise SystemExit("seed.py does not run in production")

    people = sorted(p for p in FIXTURES.glob("p*") if p.is_dir())
    if not people:
        raise SystemExit("no fixtures. Run scripts/make_fixtures.py first")

    rng = random.Random(4417)  # noqa: S311
    ml, storage = get_ml(), get_storage()
    with new_session() as db:
        users = seed_users(db)
        if db.scalar(select(Person).limit(1)) is None:
            analyst = users["ANA-2001"]
            for index, folder in enumerate(people[: -args.heldout], start=1):
                seed_person(db, folder, index, analyst, rng, ml, storage)
                if index % 10 == 0:
                    print(f"seeded {index}", flush=True)
        count = len(db.scalars(select(Person.id)).all())
        images = len(db.scalars(select(PersonImage.id)).all())
    print(f"{count} records, {images} images\n")
    print(f"password for every dev account: {DEV_PASSWORD}")
    for officer_id, _, role, secret in DEV_USERS:
        print(f"  {officer_id:<10}{role.value:<9}TOTP secret {secret}  now {pyotp.TOTP(secret).now()}")


if __name__ == "__main__":
    main()

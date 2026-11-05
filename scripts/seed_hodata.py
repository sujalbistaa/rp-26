"""Enrol a folder of real photos into a separate database. Refuses the synthetic database.

    RKL_DATABASE_URL=.../rkl_ho RKL_STORAGE_DIR=.../data/evidence_ho \
        uv run python ../../scripts/seed_hodata.py --root /path/to/people --probes /path/to/out

One sub-folder per person, files ending in _<view>.jpg. Filenames hold real names and record
numbers, so none of that is stored or printed: people become "Record Subject 001" and photos
get neutral filenames. No case, address or ID data is invented. The --holdout view stays out
of the gallery and is copied to --probes, so a search uses a photo the gallery has never seen.
"""

import argparse
import shutil
from pathlib import Path

from _paths import ROOT  # noqa: F401

from sqlalchemy import create_engine, make_url, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Calibration, ImageSource, Person, PersonImage  # noqa: E402
from app.db.session import new_session  # noqa: E402
from app.enrollment import enroll_image, next_record_id  # noqa: E402
from app.pipeline import get_ml  # noqa: E402
from app.storage import get_storage  # noqa: E402
from seed import DEV_PASSWORD, seed_users  # noqa: E402

SYNTHETIC_DB = "rkl"
SOURCE_URL = "postgresql+psycopg://rkl:rkl@localhost:5544/rkl"
RECORD_YEAR = 2025
COPIED = (
    "embedder_version", "cos_knots", "conf_knots", "floor_cos", "possible_cos", "strong_cos",
    "floor_override_cos", "n_genuine", "n_impostor", "gallery_size", "fpir_report", "synthetic",
)  # fmt: skip


def copy_calibration(db) -> None:
    """Bands come from calibration, so reuse the active fit. It stays marked synthetic."""
    if db.scalar(select(Calibration).where(Calibration.active)) is not None:
        return
    with Session(create_engine(SOURCE_URL)) as source:
        row = source.scalar(select(Calibration).where(Calibration.active))
        if row is None:
            raise SystemExit("no active calibration in the synthetic database")
        values = {name: getattr(row, name) for name in COPIED}
    note = "Copied from the synthetic database. Thresholds were not fitted on these photos."
    db.add(Calibration(**values, active=True, note=note))
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--probes", type=Path, required=True)
    parser.add_argument("--holdout", default="FB")
    args = parser.parse_args()

    settings = get_settings()
    if settings.env == "production":
        raise SystemExit("seed_hodata.py does not run in production")
    if make_url(settings.database_url).database == SYNTHETIC_DB:
        raise SystemExit("refusing the synthetic database. Set RKL_DATABASE_URL to a separate one")

    folders = sorted(p for p in args.root.iterdir() if p.is_dir())
    args.probes.mkdir(parents=True, exist_ok=True)
    ml, storage = get_ml(), get_storage()
    tally = {"ENROLLED": 0, "SKIPPED": 0, "REJECTED": 0, "probes": 0, "no_record": 0}

    with new_session() as db:
        analyst = seed_users(db)["ANA-2001"]
        copy_calibration(db)
        if db.scalar(select(Person).limit(1)) is not None:
            raise SystemExit("this database already has records")

        for number, folder in enumerate(folders, start=1):
            label = f"{number:03d}"
            person = Person(
                record_id=next_record_id(db, RECORD_YEAR),
                full_name=f"Record Subject {label}",
                created_by=analyst.id,
            )
            db.add(person)
            db.flush()
            enrolled = 0
            for path in sorted(folder.glob("*.jpg")):
                view = path.stem.rsplit("_", 1)[-1].strip()
                if view == args.holdout:
                    shutil.copyfile(path, args.probes / f"subject-{label}.jpg")
                    tally["probes"] += 1
                    continue
                report = enroll_image(
                    db,
                    data=path.read_bytes(),
                    filename=f"subject-{label}-{view}.jpg",
                    person=person,
                    source=ImageSource.MUGSHOT,
                    user=analyst,
                    ml=ml,
                    storage=storage,
                    settings=settings,
                )
                tally[report.status] += 1
                enrolled += report.status == "ENROLLED"
            if enrolled == 0:
                tally["no_record"] += 1
                db.rollback()
                continue
            db.commit()

        people = len(db.scalars(select(Person.id)).all())
        photos = len(db.scalars(select(PersonImage.id)).all())

    print(tally)
    print(f"{people} records, {photos} images, password for dev accounts: {DEV_PASSWORD}")


if __name__ == "__main__":
    main()

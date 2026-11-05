"""Re-embed the gallery into the other versioned column, after a model change.

    uv run python scripts/reembed.py --to embedding_v2

Set RKL_EMBEDDER_FILE and RKL_EMBEDDER_VERSION to the new model first. Afterwards: refit with
calibrate.py, run eval.py, and only then flip RKL_EMBEDDING_COLUMN. Search refuses to run with
a calibration fitted on a different model.
"""

import argparse

import _paths  # noqa: F401

from app.config import get_settings  # noqa: E402
from app.db.session import new_session  # noqa: E402
from app.jobs import reembed_gallery  # noqa: E402
from app.pipeline import get_ml  # noqa: E402
from app.storage import get_storage  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", choices=["embedding_v1", "embedding_v2"], required=True)
    args = parser.parse_args()
    settings = get_settings()
    if args.to == settings.embedding_column:
        raise SystemExit(f"{args.to} is the live column. Write to the other one.")
    with new_session() as db:
        done = reembed_gallery(db, get_ml(), get_storage(), settings, args.to)
    print(f"re-embedded {done} images into {args.to}")


if __name__ == "__main__":
    main()

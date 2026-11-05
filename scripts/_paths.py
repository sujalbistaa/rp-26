"""Puts the API package on sys.path, so scripts run from the repo root or services/api."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

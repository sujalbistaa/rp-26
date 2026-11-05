"""End-to-end check against a running stack, through the web proxy. Synthetic photos only.

    uv run python scripts/e2e_check.py --base http://127.0.0.1:3100 --concurrency 20

Signs in through mock login, walks the main journey as an officer, then fires concurrent
searches. Dev only: it needs RKL_DEV_LOGIN, which production refuses. Start the API with
RKL_SEARCH_LIMIT_PER_HOUR=100000, or the rate limit stops it part way.
"""

import argparse
import statistics
import sys
import threading
import time
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data" / "fixtures" / "synthetic"
HEADERS = {"x-rkl-client": "web"}
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def client(base: str, officer: str, verify=True) -> httpx.Client:
    c = httpx.Client(base_url=base, headers=HEADERS, timeout=120, verify=verify)
    r = c.post("/api/auth/dev-login", json={"officer_id": officer})
    r.raise_for_status()
    return c


def search(c: httpx.Client, photo: Path, case="214/26", reason="Chain snatching, Sector 6") -> dict:
    r = c.post(
        "/api/search/probe",
        files={"file": (photo.name, photo.read_bytes(), "image/jpeg")},
        data={"case_number": case, "reason": reason},
    )
    r.raise_for_status()
    body = r.json()
    r = c.post(f"/api/search/{body['id']}/run", json={"face_index": 0})
    r.raise_for_status()
    return r.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:3100")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--per-officer", type=int, default=10)
    args = parser.parse_args()

    people = sorted(p for p in FIXTURES.glob("p*") if p.is_dir())
    enrolled, unknown = people[:-40], people[-40:]
    pages = httpx.Client(base_url=args.base, timeout=120)
    for path in ("/sign-in",):
        status = pages.get(path).status_code
        check(f"web page {path}", status == 200, str(status))
    check("no session gets 401", pages.get("/api/search").status_code == 401)

    officer = client(args.base, "OFF-1001")
    missing = officer.post(
        "/api/search/probe",
        files={"file": ("a.jpg", (enrolled[0] / "phone_0.jpg").read_bytes(), "image/jpeg")},
        data={"case_number": "", "reason": ""},
    )
    check("search without a case number refused", missing.status_code == 422, str(missing.status_code))

    hits = strong = 0
    for folder in enrolled[:20]:
        result = search(officer, folder / "phone_0.jpg")
        band = result["people"][0]["band"] if result["people"] else "NO MATCH"
        strong += band == "STRONG"
        hits += bool(result["people"])
    check("enrolled people are found", hits == 20, f"{hits}/20 found, {strong}/20 STRONG")
    check("results carry calibration and synthetic flag",
          result["calibration_version"] is not None and result["calibration_synthetic"] is True)
    top = result["people"][0]
    record = officer.get(f"/api/records/{top['person_id']}")
    check("record opens", record.status_code == 200 and "full_name" in record.json())
    image = officer.get(f"/api/records/images/{top['images'][0]['image_id']}?size=128")
    check("gallery image renders", image.status_code == 200 and image.content[:2] == b"\xff\xd8")
    check("results page view", officer.get(f"/api/search/{result['id']}").status_code == 200)
    check("officer can't read the audit log", officer.get("/api/audit").status_code == 403)

    bands: dict[str, int] = {}
    for folder in unknown:
        result = search(officer, folder / "mugshot_0.jpg")
        best = result["people"][0]["band"] if result["people"] else "NO MATCH"
        bands[best] = bands.get(best, 0) + 1
    check("unknown people rarely reach STRONG", bands.get("STRONG", 0) <= 1, str(bands))

    admin = client(args.base, "ADM-3001")
    log = admin.get("/api/audit", params={"limit": 5})
    check("admin reads audit", log.status_code == 200)

    times: list[float] = []
    errors: list[str] = []

    def run(n: int) -> None:
        c = client(args.base, "OFF-1001")
        rng = np.random.default_rng(n)
        for _ in range(args.per_officer):
            photo = enrolled[int(rng.integers(len(enrolled)))] / "phone_0.jpg"
            start = time.perf_counter()
            try:
                search(c, photo)
                times.append(1000 * (time.perf_counter() - start))
            except httpx.HTTPError as e:
                errors.append(str(e)[:120])

    for officers in (1, 5, args.concurrency):
        times.clear()
        errors.clear()
        threads = [threading.Thread(target=run, args=(i,)) for i in range(officers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if times:
            p95 = float(np.percentile(times, 95))
            check(f"{officers} concurrent officers without errors", not errors, str(errors[:1]))
            print(f"LATENCY {officers} officers: {len(times)} searches, p50 "
                  f"{statistics.median(times):.0f} ms, p95 {p95:.0f} ms, errors {len(errors)} "
                  f"{errors[:1]}", flush=True)
        else:
            check(f"{officers} concurrent officers", False, f"no searches succeeded {errors[:1]}")

    failed = [c for c in checks if not c[1]]
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

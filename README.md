# Rourkela Police / Face Match Portal

An officer uploads a photo of a suspect. The system finds the face, compares it against the enrolled records gallery, and returns every matching image, ranked, with a calibrated confidence score.

**The output is an investigative lead, never an identification.** A human checks every match before anyone acts on it.


## Before this goes near real data

| Blocker | Why | Who |
|---------|-----|-----|
| Model licence | `buffalo_l` weights are licensed for non-commercial research. The API refuses to start in production until `RKL_MODEL_LICENCE_CONFIRMED=true`. | Department legal |
| Real calibration | The dev curve is fitted on synthetic faces. Production refuses to search with a synthetic calibration. Refit on the real gallery. | Analyst + admin |
| Written authorisation | The department's approval, a DPDP Act 2023 review and a data retention decision. | Department |
| Band policy | Bands are per search (FPIR), and officers see band and rank, not a percentage. Thresholds are synthetic until refitted, and only hold near the calibrated gallery size. The department should review this before anyone relies on them. | Department + project lead |
| Odia copy | Headings need review by a native reader. | Department |

## Local development

Needs Docker, [uv](https://docs.astral.sh/uv/) and pnpm. Model weights go in `data/models/` (`det_10g.onnx`, `w600k_r50.onnx`).

```bash
docker compose -f infra/docker-compose.yml up -d db redis

cd services/api
echo 'RKL_COOKIE_SECURE=false' > .env          # local http only
uv sync && uv run alembic upgrade head          # on Linux this installs the CUDA libraries
uv run python scripts/make_fixtures.py         # synthetic faces, nobody real
uv run python scripts/calibrate.py --pairs data/eval/pairs.csv --synthetic --activate --gallery-size 1100
uv run python scripts/seed.py                  # prints dev logins
uv run uvicorn app.main:app --reload --port 8100

cd apps/web && pnpm i && pnpm dev              # http://localhost:3100
```

Dev logins are printed by `seed.py`. `uv run python scripts/totp.py` prints current 2FA codes.

For one-click sign-in, add `RKL_DEV_LOGIN=true` to `services/api/.env` and run `pnpm dev`. The sign-in page then lists every active account, with no password or 2FA. Production refuses to start with this flag, and production web builds don't include the buttons.

Run bulk enrollment locally with `uv run python -m app.worker`. On macOS, set `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` first.

The API runs on 8100, the web app on 3100, Postgres on 5544 and Redis on 6390.

`docs/DEMO.md` is a five-minute walkthrough, and `scripts/demo.sh` starts everything for it. On Windows, run the repo inside WSL2 Ubuntu. The `services/api/data` and `scripts` symlinks and the CUDA wheels need Linux. If downloads crawl in WSL on Wi-Fi, set `networkingMode=mirrored` and `dnsTunneling=true` in `.wslconfig`.

## Checks

All of these must pass before a task is done.

```bash
cd services/api && uv run pytest && uv run ruff check .
cd apps/web && pnpm lint && pnpm typecheck && pnpm lint:design
```

Tests need the dev database running. Most use a fake detector and embedder on coloured blocks. `test_ml_real.py` runs the real models on the synthetic fixtures, and skips if they're missing.

## Calibration and evaluation

```bash
uv run python scripts/calibrate.py --pairs data/eval/pairs.csv --activate
uv run python scripts/eval.py --baseline data/eval/baseline.json
```

- The pairs file has columns `a,b,same`, with paths relative to `data/`.
- `calibrate.py` refuses fewer than 100,000 impostor pairs, and fewer than 3 / target non-mated searches for an FPIR target: 3,000 for STRONG at 0.1%. It also refuses a curve where STRONG would show a low confidence.
- `--gallery-size` sets the gallery the bands are for. The default is the current count in the database. It can't exceed the photos available to simulate it.
- Photos need one folder per person, because the FPIR simulation reads the person from the folder.
- `eval.py` exits 1 if any slice drops more than 2 points against the baseline.
- `scripts/reembed.py` moves the gallery to a new model. Search refuses to run if the active calibration was fitted on a different model.

## Production

```bash
scripts/gen_certs.sh facematch.example   # or mount department certificates at infra/certs
cp .env.production.example .env.production
docker compose --env-file .env.production -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d --build
```

The API refuses to start if a dev key, plain transport or an unconfirmed model licence is configured. `infra/docker-compose.staging.yml` runs the same containers with `RKL_ENV=dev`, so a synthetic demo can search. Never use it with real data.

| Concern | How it's handled |
|---------|------------------|
| Transport | TLS on every hop. Postgres `verify-full`, Redis `rediss://`, edge TLS 1.3. |
| At rest | Images: AES-256-GCM in the API, before storage. Address, phone, ID number, TOTP secret: AES-256-GCM per column. Put the Postgres volume on an encrypted disk (LUKS). |
| Audit | Database triggers reject UPDATE, DELETE and TRUNCATE. The app connects as `rkl_app`, which can't touch the triggers. |
| Network | App containers sit on an `internal` Docker network with no route out. A strict CSP blocks external requests in the browser. |
| Maintenance | Keep autovacuum on. Dead rows lower HNSW recall until they're vacuumed. |

**Back up `RKL_FIELD_KEY` and `RKL_STORAGE_KEY` offline.** Lose them and the evidence is gone. `scripts/backup.sh` backs up Postgres and evidence, without keys. `scripts/backup.sh --verify <dir>` restores into a scratch database and compares counts.

### GPU

`onnxruntime-gpu[cuda,cudnn]` installs on Linux x86-64 and brings its own CUDA libraries, so `python:3.12-slim` works with no `nvidia/cuda` base. `api` and `worker` reserve the GPU in the production compose file and set `RKL_REQUIRE_GPU=true`, so startup fails if CUDA isn't active. `scripts/gpu_check.py` compares CPU and CUDA. Inference is serialised in `app/ml/runtime.py`, because concurrent calls collapsed latency.

### Storage

`RKL_STORAGE_BACKEND=file` writes encrypted objects to a volume. `RKL_STORAGE_BACKEND=s3` with `RKL_S3_ENDPOINT` uses any S3-compatible server. Objects are encrypted before they leave the API either way.

## Repo

```
apps/web/        Next.js 15. tokens.css is the single source of design truth.
services/api/    FastAPI, ML pipeline, migrations, tests.
scripts/         Fixtures, seed, calibrate, eval, GPU and scale checks, backups, certificates, design lint.
research/reid/  Separate body re-ID experiment on Market-1501. Not part of the portal.
docs/           Project report, demo guide, DPDP checklist, measured results.
infra/           Compose, nginx, database role setup.
data/            Gitignored. Never commit faces.
```

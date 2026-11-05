# Rourkela Police / Face Match Portal

An officer uploads a photo of a suspect. The system finds the face, compares it against the enrolled records gallery, and returns every matching image, ranked, with a calibrated confidence score.

**The output is an investigative lead, never an identification.** A human checks every match before anyone acts on it.

`CLAUDE.md` holds the full spec and design system. This file covers running it.

## Before this goes near real data

| Blocker | Why | Who |
|---------|-----|-----|
| Model licence | `buffalo_l` weights are licensed for non-commercial research. The API refuses to start in production until `RKL_MODEL_LICENCE_CONFIRMED=true`. | Department legal |
| Real calibration | The dev curve is fitted on synthetic faces. Production refuses to search with a synthetic calibration. Refit on the real gallery. | Analyst + admin |
| Written authorisation | The department's approval, a DPDP Act 2023 review and a data retention decision. | Department |
| Band policy | False-match rates are per comparison, not per search. At 18,204 images, 1 in 10,000 means about 1.8 false STRONG matches per search. Confidence assumes a 50/50 prior, so a wrong person can read above 99%. Decide what officers should see before anyone relies on these numbers. | Department + project lead |
| Odia copy | Headings need review by a native reader. | Department |

## Local development

Needs Docker, [uv](https://docs.astral.sh/uv/) and pnpm. Model weights go in `data/models/` (`det_10g.onnx`, `w600k_r50.onnx`).

```bash
docker compose -f infra/docker-compose.yml up -d db redis

cd services/api
echo 'RKL_COOKIE_SECURE=false' > .env          # local http only
uv sync && uv run alembic upgrade head
uv run python scripts/make_fixtures.py         # synthetic faces, nobody real
uv run python scripts/calibrate.py --pairs data/eval/pairs.csv --synthetic --activate
uv run python scripts/seed.py                  # prints dev logins
uv run uvicorn app.main:app --reload --port 8100

cd apps/web && pnpm i && pnpm dev              # http://localhost:3100
```

Dev logins are printed by `seed.py`. `uv run python scripts/totp.py` prints current 2FA codes.

For one-click sign-in, add `RKL_DEV_LOGIN=true` to `services/api/.env` and run `pnpm dev`. The sign-in page then lists every active account, with no password or 2FA. Production refuses to start with this flag, and production web builds don't include the buttons.

Run bulk enrollment locally with `uv run python -m app.worker`. On macOS, set `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` first.

The API runs on 8100, the web app on 3100, Postgres on 5544 and Redis on 6390.

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
- `calibrate.py` refuses fewer than 100,000 impostor pairs, because a false-match rate of 1 in 10,000 can't be estimated from less. It also refuses a curve where STRONG would show a low confidence.
- `eval.py` exits 1 if any slice drops more than 2 points against the baseline.
- `scripts/reembed.py` moves the gallery to a new model. Search refuses to run if the active calibration was fitted on a different model.

## Production

```bash
scripts/gen_certs.sh facematch.example   # or mount department certificates at infra/certs
cp .env.production.example .env.production
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d --build
```

The API refuses to start if a dev key, plain transport or an unconfirmed model licence is configured.

| Concern | How it's handled |
|---------|------------------|
| Transport | TLS on every hop. Postgres `verify-full`, Redis `rediss://`, edge TLS 1.3. |
| At rest | Images: AES-256-GCM in the API, before storage. Address, phone, ID number, TOTP secret: AES-256-GCM per column. Put the Postgres volume on an encrypted disk (LUKS). |
| Audit | Database triggers reject UPDATE, DELETE and TRUNCATE. The app connects as `rkl_app`, which can't touch the triggers. |
| Network | App containers sit on an `internal` Docker network with no route out. A strict CSP blocks external requests in the browser. |
| Maintenance | Keep autovacuum on. Dead rows lower HNSW recall until they're vacuumed. |

**Back up `RKL_FIELD_KEY` and `RKL_STORAGE_KEY` offline.** Lose them and the evidence is gone.

### GPU

`onnxruntime-gpu` installs on Linux x86-64, and the code prefers CUDA. The stock `python:3.12-slim` image has no CUDA libraries, so it falls back to CPU (about 300 ms per image). For GPU, build on an `nvidia/cuda` runtime base and add a device reservation to `api` and `worker`.

### Storage

`RKL_STORAGE_BACKEND=file` writes encrypted objects to a volume. `RKL_STORAGE_BACKEND=s3` with `RKL_S3_ENDPOINT` uses any S3-compatible server. Objects are encrypted before they leave the API either way.

## Repo

```
apps/web/        Next.js 15. tokens.css is the single source of design truth.
services/api/    FastAPI, ML pipeline, migrations, tests.
scripts/         Fixtures, seed, calibrate, eval, reembed, certificates, design lint.
infra/           Compose, nginx, database role setup.
data/            Gitignored. Never commit faces.
```

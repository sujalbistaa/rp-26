#!/usr/bin/env bash
# Start everything the demo needs, in dev mode on synthetic data. Ctrl+C stops the app.
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH" UV_NO_SYNC=1 COREPACK_ENABLE_DOWNLOAD_PROMPT=0
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

docker compose -p rklprod --env-file .env.production -f infra/docker-compose.yml \
  -f infra/docker-compose.prod.yml stop >/dev/null 2>&1 || true
docker compose -f infra/docker-compose.yml up -d --wait db redis

pkill -f 'uvicorn app.main' 2>/dev/null
pkill -f 'next dev' 2>/dev/null
pkill -f 'app.worker' 2>/dev/null
sleep 1

cd services/api
uv run --no-sync alembic upgrade head
uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8100 &
uv run --no-sync python -m app.worker &
cd ../../apps/web
pnpm dev &
trap 'kill $(jobs -p) 2>/dev/null' EXIT

until curl -sf -o /dev/null http://127.0.0.1:8100/healthz; do sleep 1; done
until curl -sf -o /dev/null http://127.0.0.1:3100/sign-in; do sleep 1; done
echo "Ready: http://localhost:3100"
wait

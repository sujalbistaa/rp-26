#!/bin/bash
# Runs the portal on the separate real-photo database. The synthetic app on 3100 is untouched.
#   bash scripts/start_real.sh     then open http://127.0.0.1:3101
cd "$(dirname "$0")/.."
ROOT=$PWD
export UV_NO_SYNC=1 RKL_DATABASE_URL=postgresql+psycopg://rkl:rkl@localhost:5544/rkl_ho
export RKL_STORAGE_DIR=$ROOT/data/evidence_ho RKL_SEARCH_LIMIT_PER_HOUR=100000
(cd services/api && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8101) &
API=$!
trap 'kill $API' EXIT
cd apps/web
RKL_API_URL=http://127.0.0.1:8101 RKL_DIST_DIR=.next-real NEXT_TELEMETRY_DISABLED=1 \
  ./node_modules/.bin/next dev -H 127.0.0.1 -p 3101

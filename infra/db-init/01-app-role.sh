#!/bin/bash
# The app connects as rkl_app. It can't alter tables, so it can't drop the audit triggers.
set -euo pipefail
: "${RKL_APP_DB_PASSWORD:?set RKL_APP_DB_PASSWORD}"
psql -v ON_ERROR_STOP=1 -v pw="$RKL_APP_DB_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE rkl_app LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE rkl TO rkl_app;
GRANT USAGE ON SCHEMA public TO rkl_app;
SQL

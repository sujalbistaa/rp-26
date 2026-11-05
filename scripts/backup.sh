#!/usr/bin/env bash
# Back up Postgres and the encrypted evidence store. Keys are never included:
# RKL_FIELD_KEY and RKL_STORAGE_KEY must be kept separately, offline.
#
#   scripts/backup.sh [dest]                 write dest/rkl-YYYYmmdd-HHMMSS/
#   scripts/backup.sh --restore <dir> <db>   restore a dump into a new, empty database <db>
#   scripts/backup.sh --verify <dir>         restore into a scratch database and compare counts
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.yml")
EVIDENCE="${RKL_STORAGE_DIR:-$ROOT/data/evidence}"
PSQL=("${COMPOSE[@]}" exec -T db psql -U rkl -v ON_ERROR_STOP=1)

restore() {
  local dir="$1" db="$2"
  "${PSQL[@]}" -d postgres -c "CREATE DATABASE $db"
  "${COMPOSE[@]}" exec -T db pg_restore -U rkl -d "$db" --no-owner --exit-on-error < "$dir/db.dump"
}

counts() {
  "${PSQL[@]}" -d "$1" -Atc "SELECT
    (SELECT count(*) FROM persons), (SELECT count(*) FROM person_images),
    (SELECT count(*) FROM audit_log), (SELECT count(*) FROM calibration)"
}

case "${1:-}" in
  --restore)
    restore "$2" "$3"
    echo "restored into $3"
    ;;
  --verify)
    dir="$2"
    scratch="rkl_verify_$$"
    trap '"${PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS $scratch" >/dev/null' EXIT
    restore "$dir" "$scratch"
    live="$(counts rkl)"
    restored="$(counts "$scratch")"
    echo "live     persons|images|audit|calibration: $live"
    echo "restored persons|images|audit|calibration: $restored"
    (cd "$dir" && sha256sum -c --quiet SHA256SUMS)
    tar -tzf "$dir/evidence.tar.gz" > /dev/null
    [ "$live" = "$restored" ] && echo "backup verified" || { echo "COUNTS DIFFER"; exit 1; }
    ;;
  *)
    dest="${1:-$ROOT/data/backups}/rkl-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$dest"
    "${COMPOSE[@]}" exec -T db pg_dump -U rkl -d rkl -Fc > "$dest/db.dump"
    tar -czf "$dest/evidence.tar.gz" -C "$(dirname "$EVIDENCE")" "$(basename "$EVIDENCE")"
    (cd "$dest" && sha256sum db.dump evidence.tar.gz > SHA256SUMS)
    echo "backup written to $dest"
    ;;
esac

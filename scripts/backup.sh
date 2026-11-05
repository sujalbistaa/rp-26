#!/usr/bin/env bash
# Back up Postgres and the encrypted evidence store. Keys are never included:
# RKL_FIELD_KEY and RKL_STORAGE_KEY must be kept separately, offline.
#
# The dump holds face embeddings and names in the clear (address, phone and ID stay encrypted,
# as do the images). Write it only to an encrypted disk.
#
#   scripts/backup.sh [dest]                 write dest/rkl-YYYYmmdd-HHMMSS/
#   scripts/backup.sh --restore <dir> <db>   restore a dump into a new, empty database <db>
#   scripts/backup.sh --verify <dir>         restore into a scratch database, compare with the
#                                            counts taken when the backup was written
#
# RKL_BACKUP_TARGET=prod backs up the production stack, with evidence from its Docker volume.
# Without it, the dev stack and data/evidence are used.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${RKL_BACKUP_TARGET:-dev}"
if [ "$TARGET" = prod ]; then
  COMPOSE=(docker compose -p rklprod --env-file "$ROOT/.env.production"
    -f "$ROOT/infra/docker-compose.yml" -f "$ROOT/infra/docker-compose.prod.yml")
else
  COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.yml")
fi
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
    (cd "$dir" && sha256sum -c --quiet SHA256SUMS)
    tar -tzf "$dir/evidence.tar.gz" > /dev/null
    restore "$dir" "$scratch"
    expected="$(cat "$dir/counts.txt")"
    restored="$(counts "$scratch")"
    echo "at backup persons|images|audit|calibration: $expected"
    echo "restored  persons|images|audit|calibration: $restored"
    [ "$expected" = "$restored" ] && echo "backup verified" || { echo "COUNTS DIFFER"; exit 1; }
    ;;
  *)
    dest="${1:-$ROOT/data/backups}/rkl-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$dest"
    chmod 700 "$dest"
    "${COMPOSE[@]}" exec -T db pg_dump -U rkl -d rkl -Fc > "$dest/db.dump"
    counts rkl > "$dest/counts.txt"
    if [ "$TARGET" = prod ]; then
      "${COMPOSE[@]}" exec -T api tar -czf - -C / evidence > "$dest/evidence.tar.gz"
    else
      tar -czf "$dest/evidence.tar.gz" -C "$(dirname "$EVIDENCE")" "$(basename "$EVIDENCE")"
    fi
    (cd "$dest" && sha256sum db.dump evidence.tar.gz counts.txt > SHA256SUMS)
    echo "backup written to $dest"
    ;;
esac

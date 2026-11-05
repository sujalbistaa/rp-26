#!/usr/bin/env bash
# Generates an internal CA and a certificate per service in infra/certs/.
# Use department-issued certificates instead if there's an internal PKI.
#   scripts/gen_certs.sh [edge-hostname]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/infra/certs"
EDGE="${1:-facematch.local}"
mkdir -p "$OUT"
cd "$OUT"

if [ ! -f ca.key ]; then
  openssl req -x509 -newkey rsa:4096 -sha256 -days 1825 -nodes \
    -keyout ca.key -out ca.crt -subj "/O=Rourkela Police Internal/CN=RKL Internal CA"
fi

issue() {
  local name="$1" dns="$2"
  openssl req -newkey rsa:2048 -sha256 -nodes -keyout "$name.key" -out "$name.csr" -subj "/CN=$dns"
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial -days 397 -sha256 \
    -out "$name.crt" -extfile <(printf "subjectAltName=DNS:%s\n" "$dns")
  rm -f "$name.csr"
}

issue db db
issue redis redis
issue api api
issue web web
issue edge "$EDGE"

# Containers read these as non-root users. Keep infra/certs itself locked down on the host.
chmod 644 ./*.key
chmod 600 ca.key
chmod 700 .
echo "certificates in $OUT"

#!/usr/bin/env sh
set -eu
OUT="${1:-./.local-tls}"
mkdir -p "$OUT"
umask 077
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 7 \
  -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -keyout "$OUT/tls.key" -out "$OUT/tls.crt"
echo "Development certificate written to $OUT (do not commit private key)."

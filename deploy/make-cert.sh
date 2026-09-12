#!/usr/bin/env bash
# Create the self-signed certificate the AnsiWEB dashboard uses for HTTPS.
# Run again (with a new IP or name) to replace it:  sudo ./deploy/make-cert.sh 192.168.1.10
set -euo pipefail
DIR=/etc/ssl/ansiweb
CN="${1:-$(hostname -f 2>/dev/null || hostname)}"
mkdir -p "$DIR"

# Subject alternative names: the host name, every local IPv4 address, and the
# name or address given on the command line.
SANS="DNS:$(hostname),DNS:localhost,IP:127.0.0.1"
for ip in $(hostname -I 2>/dev/null); do SANS="$SANS,IP:$ip"; done
if [[ "$CN" =~ ^[0-9.]+$ ]]; then SANS="$SANS,IP:$CN"; else SANS="$SANS,DNS:$CN"; fi

openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -sha256 \
    -keyout "$DIR/server.key" -out "$DIR/server.crt" \
    -subj "/CN=$CN/O=AnsiWEB" -addext "subjectAltName=$SANS" >/dev/null 2>&1

chmod 600 "$DIR/server.key"
chmod 644 "$DIR/server.crt"
echo "Certificate created for $CN"
echo "  $DIR/server.crt   (fingerprint below)"
openssl x509 -in "$DIR/server.crt" -noout -fingerprint -sha256 | sed 's/^/  /'

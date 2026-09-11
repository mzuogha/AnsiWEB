#!/usr/bin/env bash
# AnsiWEB installer for Ubuntu Server 22.04 / 24.04 (run as root from the repo folder).
#   sudo ./install.sh            install or upgrade
set -euo pipefail

APP_DIR=/opt/ansiweb
DATA_DIR=/var/lib/ansiweb
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo ./install.sh"; exit 1; fi

echo "==> Installing system packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-dev gcc \
    libkrb5-dev nginx rsync git >/dev/null

echo "==> Creating service account"
id ansiweb &>/dev/null || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin ansiweb

echo "==> Copying application to $APP_DIR"
mkdir -p "$APP_DIR"
rsync -a --delete --exclude .git --exclude venv --exclude data --exclude ansible/collections \
      "$SRC_DIR"/ "$APP_DIR"/

echo "==> Python environment"
[[ -d "$APP_DIR/venv" ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Ansible collections"
"$APP_DIR/venv/bin/ansible-galaxy" collection install -r "$APP_DIR/ansible/requirements.yml" \
      -p "$APP_DIR/ansible/collections" --force >/dev/null

echo "==> Command-line helper /usr/local/bin/ansiweb"
cat > /usr/local/bin/ansiweb <<WRAP
#!/bin/sh
# AnsiWEB command line, e.g.: sudo ansiweb update-cache
cd $APP_DIR && exec sudo -u ansiweb ANSIWEB_DATA=$DATA_DIR HOME=$DATA_DIR $APP_DIR/venv/bin/python -m ansiweb.cli "\$@"
WRAP
chmod 755 /usr/local/bin/ansiweb

echo "==> Data folder $DATA_DIR"
mkdir -p "$DATA_DIR"
chown -R ansiweb:ansiweb "$DATA_DIR"
/usr/local/bin/ansiweb init

if [[ ! -f "$DATA_DIR/admin.json" ]]; then
  echo "==> Create the web admin login (username: admin)"
  /usr/local/bin/ansiweb set-password admin
fi

# Office deployment was removed in v1.1; its cached files are no longer used.
if [[ -d "$DATA_DIR/cache/office" ]]; then
  echo "==> Removing the unused Office cache ($(du -sh "$DATA_DIR/cache/office" | cut -f1))"
  rm -rf "$DATA_DIR/cache/office" "$DATA_DIR/office_staging" "$DATA_DIR/office_pull.json"
fi

echo "==> Service and web server"
install -m 0644 "$APP_DIR/deploy/ansiweb.service" /etc/systemd/system/ansiweb.service
install -m 0644 "$APP_DIR/deploy/nginx-ansiweb.conf" /etc/nginx/sites-available/ansiweb
ln -sf /etc/nginx/sites-available/ansiweb /etc/nginx/sites-enabled/ansiweb
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl daemon-reload
systemctl enable -q --now ansiweb
systemctl restart ansiweb
systemctl reload nginx

IP=$(hostname -I | awk '{print $1}')
echo
echo "AnsiWEB is running:  http://$IP/"
echo "Sign in as 'admin', then follow the setup notes on the dashboard."

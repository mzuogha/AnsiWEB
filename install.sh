#!/usr/bin/env bash
# AnsiWEB installer for Ubuntu 22.04 / 24.04, on a server or under WSL2.
#   sudo ./install.sh            install or upgrade
# See docs/INSTALL.md for the full guide.
set -euo pipefail

APP_DIR=/opt/ansiweb
DATA_DIR=/var/lib/ansiweb
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo ./install.sh"; exit 1; fi

IS_WSL=no
grep -qi microsoft /proc/version 2>/dev/null && IS_WSL=yes
HAS_SYSTEMD=no
[[ -d /run/systemd/system ]] && command -v systemctl >/dev/null && HAS_SYSTEMD=yes

if [[ $IS_WSL == yes ]]; then
  echo "==> Running under WSL"
  if [[ $HAS_SYSTEMD == no ]]; then
    cat <<'MSG'
    systemd is not running in this WSL distribution, so AnsiWEB cannot be
    installed as a background service. Enable it first:

      printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf
      # then, in Windows PowerShell:   wsl --shutdown
      # reopen Ubuntu and run this installer again.

    Continuing anyway: AnsiWEB will be installed, but you will have to start
    it by hand (the command is printed at the end).
MSG
  fi
fi

echo "==> Installing system packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-dev gcc \
    libkrb5-dev nginx rsync git openssl >/dev/null

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
if [ "\$(id -u)" -ne 0 ]; then exec sudo "\$0" "\$@"; fi
cd $APP_DIR || exit 1
exec runuser -u ansiweb -- env ANSIWEB_DATA=$DATA_DIR HOME=$DATA_DIR \\
     $APP_DIR/venv/bin/python -m ansiweb.cli "\$@"
WRAP
chmod 755 /usr/local/bin/ansiweb

echo "==> Data folder $DATA_DIR"
mkdir -p "$DATA_DIR"
chown -R ansiweb:ansiweb "$DATA_DIR"
/usr/local/bin/ansiweb init

NEED_PASSWORD=no
if [[ ! -f "$DATA_DIR/admin.json" ]]; then
  if [[ -t 0 ]]; then
    echo "==> Create the web admin login (username: admin)"
    /usr/local/bin/ansiweb set-password admin
  else
    NEED_PASSWORD=yes   # not running interactively; set it afterwards
  fi
fi

# Office deployment was removed in v1.1; its cached files are no longer used.
if [[ -d "$DATA_DIR/cache/office" ]]; then
  echo "==> Removing the unused Office cache ($(du -sh "$DATA_DIR/cache/office" | cut -f1))"
  rm -rf "$DATA_DIR/cache/office" "$DATA_DIR/office_staging" "$DATA_DIR/office_pull.json"
fi

echo "==> HTTPS certificate"
if [[ -f /etc/ssl/ansiweb/server.crt ]]; then
  echo "    keeping the existing certificate in /etc/ssl/ansiweb"
else
  "$APP_DIR/deploy/make-cert.sh" "$(hostname -I | awk '{print $1}')"
fi

echo "==> Web server"
install -m 0644 "$APP_DIR/deploy/nginx-ansiweb.conf" /etc/nginx/sites-available/ansiweb
ln -sf /etc/nginx/sites-available/ansiweb /etc/nginx/sites-enabled/ansiweb
rm -f /etc/nginx/sites-enabled/default
nginx -t

IP=$(hostname -I | awk '{print $1}')

if [[ $HAS_SYSTEMD == yes ]]; then
  echo "==> Service"
  install -m 0644 "$APP_DIR/deploy/ansiweb.service" /etc/systemd/system/ansiweb.service
  systemctl daemon-reload
  systemctl enable -q --now ansiweb
  systemctl restart ansiweb
  systemctl enable -q nginx
  systemctl restart nginx
  sleep 2
  systemctl is-active --quiet ansiweb || { echo "AnsiWEB failed to start. Check: journalctl -u ansiweb -n 40"; exit 1; }
  STARTED="AnsiWEB is running:  https://$IP/"
else
  service nginx restart >/dev/null 2>&1 || nginx -s reload || nginx
  STARTED=$(cat <<MSG
AnsiWEB is installed but not started (no systemd on this system).
Start it with:
  sudo runuser -u ansiweb -- env ANSIWEB_DATA=$DATA_DIR HOME=$DATA_DIR \\
       $APP_DIR/venv/bin/gunicorn --chdir $APP_DIR --workers 1 --threads 16 \\
       --timeout 7200 --bind 127.0.0.1:8081 "ansiweb.web:create_app()"
Then open:  https://$IP/
MSG
)
fi

echo
echo "$STARTED"
echo "The certificate is self-signed, so the browser warns once; check the fingerprint with:"
echo "  sudo openssl x509 -in /etc/ssl/ansiweb/server.crt -noout -fingerprint -sha256"
if [[ $NEED_PASSWORD == yes ]]; then
  echo "Set the web admin password before signing in:"
  echo "  sudo ansiweb set-password admin"
else
  echo "Sign in as 'admin', then follow the setup notes on the dashboard."
fi

if [[ $IS_WSL == yes ]]; then
  cat <<MSG

WSL note: $IP is an internal WSL address that your PCs cannot reach.
Forward port 80 from Windows to WSL, in an *Administrator* PowerShell:

  netsh interface portproxy add v4tov4 listenport=80  listenaddress=0.0.0.0 connectport=80  connectaddress=$IP
  netsh interface portproxy add v4tov4 listenport=443 listenaddress=0.0.0.0 connectport=443 connectaddress=$IP
  New-NetFirewallRule -DisplayName "AnsiWEB" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow

Port 80 serves the installer cache to the PCs; 443 is the dashboard.
Then use the *Windows* machine's IP as the server IP in AnsiWEB Settings.
This has to be redone whenever the WSL address changes - see docs/INSTALL.md.
MSG
fi

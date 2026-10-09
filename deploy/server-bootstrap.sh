#!/bin/bash
# One-time server setup for BigScreen v2. Run as:  sudo bash ~/bigscreen/deploy/server-bootstrap.sh
#
#  1. Installs + starts bigscreen-hub.service (the v2 hub).
#  2. Stops + disables the v1 services (bigscreen-server, bigscreen-control); their files are left in place.
#  3. Lets user scott start/stop/restart bigscreen-hub and read service logs without a password,
#     so code updates can be rolled out without you typing sudo. Nothing else is granted.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
REPO=/home/scott/bigscreen

install -m 644 "$REPO/deploy/bigscreen-hub.service" /etc/systemd/system/bigscreen-hub.service

# Exact commands only (a wildcard would also match extra unit names as arguments).
cat > /etc/sudoers.d/bigscreen <<'EOF'
# BigScreen: allow scott to control only the BigScreen hub service without a password
scott ALL=(root) NOPASSWD: /usr/bin/systemctl restart bigscreen-hub, /usr/bin/systemctl start bigscreen-hub, /usr/bin/systemctl stop bigscreen-hub, /usr/bin/systemctl restart bigscreen-hub.service
EOF
chmod 440 /etc/sudoers.d/bigscreen
visudo -cf /etc/sudoers.d/bigscreen

usermod -aG systemd-journal scott      # read service logs without sudo

systemctl daemon-reload
systemctl disable --now bigscreen-server.service bigscreen-control.service || true
systemctl enable --now bigscreen-hub.service
sleep 3
systemctl --no-pager --lines=5 status bigscreen-hub.service || true
echo
echo "Done. Control panel: http://192.168.1.111:8800"

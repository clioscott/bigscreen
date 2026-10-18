#!/bin/bash
# One-time: install the pull-based deploy timer on the server.  sudo bash ~/bigscreen/deploy/deploy-setup.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
REPO=/home/scott/bigscreen
install -m 644 "$REPO/deploy/bigscreen-deploy.service" /etc/systemd/system/bigscreen-deploy.service
install -m 644 "$REPO/deploy/bigscreen-deploy.timer" /etc/systemd/system/bigscreen-deploy.timer
systemctl daemon-reload
systemctl enable --now bigscreen-deploy.timer
systemctl --no-pager list-timers bigscreen-deploy.timer
echo "Deploy log: ~/bigscreen-state/deploy.log  ·  journalctl -u bigscreen-deploy"

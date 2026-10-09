#!/bin/bash
# Restart the hub. Uses the systemd unit once installed (deploy/server-bootstrap.sh);
# until then, (re)launches it as a detached background process.
cd ~/bigscreen
if systemctl list-unit-files bigscreen-hub.service >/dev/null 2>&1 && systemctl is-enabled bigscreen-hub >/dev/null 2>&1; then
    sudo systemctl restart bigscreen-hub && echo "restarted bigscreen-hub (systemd)"
    exit
fi
for p in $(pgrep -f "python -m hub.main"); do kill "$p"; done
sleep 2
nohup setsid .venv/bin/python -m hub.main >> ~/bigscreen-state/hub-manual.log 2>&1 < /dev/null &
sleep 4
curl -s -o /dev/null -w "hub panel HTTP %{http_code}\n" http://localhost:8800/

#!/bin/bash
# Pull-based deploy, run every minute by bigscreen-deploy.timer on the server (as scott).
#
# When origin/main (GitHub) has moved: fast-forward ~/bigscreen, pip-install if requirements changed, restart the
# hub, then update the Pi (its clone pulls from this server's ~/bigscreen) and restart the display if display code
# changed. Each deploy is appended to ~/bigscreen-state/deploy.log and summarised in ~/bigscreen-state/deploy.json,
# which the control panel's Overview shows. Never resets or discards anything: a dirty tree or a non-fast-forward
# stops the deploy and is reported.
set -uo pipefail

REPO=${BIGSCREEN_REPO:-$HOME/bigscreen}
STATE=${BIGSCREEN_STATE:-$HOME/bigscreen-state}
PI=${BIGSCREEN_PI:-scott@192.168.1.100}
LOG=$STATE/deploy.log
JSON=$STATE/deploy.json
LOCK=$STATE/deploy.lock

exec 9>"$LOCK"
flock -n 9 || exit 0                                   # a deploy is still running

cd "$REPO" || exit 1
git fetch -q origin main 2>>"$LOG" || { echo "$(date -Is) fetch failed" >>"$LOG"; exit 1; }
old=$(git rev-parse HEAD)
new=$(git rev-parse origin/main)
[ "$old" = "$new" ] && exit 0

say() { echo "$(date -Is) $*" | tee -a "$LOG"; }
record() {  # ok hub pi message
    python3 - "$JSON" "$new" "$old" "$1" "$2" "$3" "$4" "$(git log -1 --format=%s "$new")" <<'EOF'
import json, sys, time
path, new, old, ok, hub, pi, msg, subject = sys.argv[1:]
try:
    prev = json.load(open(path))
except (OSError, ValueError):
    prev = {}
rec = {"commit": new, "from": old, "subject": subject, "at": time.time(), "ok": ok == "1", "hub": hub, "pi": pi,
       "message": msg[-2000:]}
rec["history"] = ([{k: v for k, v in rec.items() if k != "history"}] + prev.get("history", []))[:20]
json.dump(rec, open(path + ".tmp", "w"), indent=1)
import os; os.replace(path + ".tmp", path)
EOF
}

say "deploy ${old:0:7} -> ${new:0:7}: $(git log -1 --format=%s "$new")"
if ! git diff --quiet || ! git diff --cached --quiet; then
    say "ABORT: ~/bigscreen has uncommitted changes to tracked files"
    record 0 "skipped" "skipped" "Working tree has uncommitted changes: $(git status --short | head -5 | tr '\n' ' ')"
    exit 1
fi
if ! out=$(git merge --ff-only -q origin/main 2>&1); then
    say "ABORT: not a fast-forward: $out"
    record 0 "skipped" "skipped" "Not a fast-forward: $out"
    exit 1
fi
changed=$(git diff --name-only "$old" "$new")
if ! grep -qvE '^(docs/|\.github/|tests/|[^/]*\.md$|LICENSE$)' <<<"$changed"; then
    say "docs/CI only: no restarts"
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$PI" 'cd ~/bigscreen && git pull -q --ff-only' >>"$LOG" 2>&1
    record 1 "not needed" "pulled (docs only)" "$(git log --format='%h %s' "$old..$new" | head -20)"
    exit 0
fi

hub="restarted"
if grep -qx 'requirements-hub.txt' <<<"$changed"; then
    say "requirements changed: pip install"
    if ! .venv/bin/pip install -q -r requirements-hub.txt >>"$LOG" 2>&1; then
        hub="pip install failed"
    fi
fi
if ! sudo -n systemctl restart bigscreen-hub 2>>"$LOG"; then
    hub="restart failed"
fi
sleep 3
systemctl is-active -q bigscreen-hub || hub="not running after restart"

pi="up to date"
if out=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$PI" 'cd ~/bigscreen && git pull -q --ff-only 2>&1 && git rev-parse HEAD' 2>&1); then
    pi_head=$(tail -1 <<<"$out")
    if [ "$pi_head" != "$new" ]; then
        pi="pulled ${pi_head:0:7}, expected ${new:0:7}"
    elif grep -qE '^(display|common|assets)/' <<<"$changed"; then
        if ssh -o BatchMode=yes "$PI" 'sudo systemctl restart bigscreen-display' >>"$LOG" 2>&1; then
            pi="pulled, display restarted"
        else
            pi="pulled, display restart failed"
        fi
    else
        pi="pulled (no display changes)"
    fi
else
    pi="pull failed: $(tail -1 <<<"$out")"
fi

ok=1
[ "$hub" = "restarted" ] || ok=0
case "$pi" in "pulled"*"failed"* | "pull failed"* | "pulled "*"expected"*) ok=0 ;; esac
say "hub: $hub · pi: $pi"
record "$ok" "$hub" "$pi" "$(git log --format='%h %s' "$old..$new" | head -20)"

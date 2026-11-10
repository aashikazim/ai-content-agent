#!/usr/bin/env bash
# Go live with unattended automation. Run it, leave it open, Ctrl+C to stop.
#   ./scripts/go_live.sh
# Starts the existing scheduler + worker units (one scheduler only) and follows
# their logs. Ctrl+C stops both and sets the kill switch. The API keeps running.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
PY=.venv/bin/python
SCHED=ai-content-agent-scheduler.service
WORKER=ai-content-agent-worker.service
API=ai-content-agent-api.service
KILL=data/automation/KILL

[ -x "$PY" ] || { echo "Missing .venv; create it first."; exit 1; }
if pgrep -f "python.* scheduler\.py" >/dev/null && ! systemctl --user is-active -q "$SCHED"; then
  echo "A scheduler.py is already running outside systemd. Stop it first (one scheduler only)."; exit 1
fi

echo "== Configuration"
"$PY" scripts/go_live_preflight.py check

echo "== YouTube credentials"
if ! "$PY" scripts/go_live_preflight.py youtube-auth; then
  echo "Renewing YouTube OAuth: open the printed link in your browser."
  "$PY" scripts/youtube_auth.py
  "$PY" scripts/go_live_preflight.py youtube-auth || { echo "YouTube is still not authorized."; exit 1; }
fi

echo "== Upload attempts that failed before reaching the platform"
if "$PY" scripts/go_live_preflight.py list-unsent; then
  echo "These failed while refreshing credentials, so nothing was sent. They keep the platform paused."
  echo "Closing them is permanent and never retries: those jobs will not be uploaded to that platform."
  read -r -p "Have you checked the platform and confirmed they are NOT there? Close them and resume? [y/N] " answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then "$PY" scripts/go_live_preflight.py close-unsent
  else echo "Left as is: that platform stays paused; other platforms continue."; fi
else echo "None."; fi

stop() {
  trap - INT TERM
  echo; echo "== Stopping (kill switch set; an in-flight render or upload is recorded, never retried)"
  mkdir -p "$(dirname "$KILL")"; touch "$KILL"
  systemctl --user stop "$SCHED" "$WORKER" || true
  [ -n "${LOGS:-}" ] && kill "$LOGS" 2>/dev/null || true
  echo "Stopped. Run this script again to resume."
  exit 0
}
trap stop INT TERM

echo "== Starting"
rm -f "$KILL"
systemctl --user restart "$API"          # pick up current code/config
systemctl --user restart "$WORKER" "$SCHED"
sleep 3
systemctl --user is-active "$API" "$WORKER" "$SCHED" || { echo "A service failed to start; see logs below."; }
echo "Live. Dashboard: http://127.0.0.1:8000  |  Ctrl+C to stop."
journalctl --user -f -n 20 -u "$SCHED" -u "$WORKER" &
LOGS=$!
wait "$LOGS"

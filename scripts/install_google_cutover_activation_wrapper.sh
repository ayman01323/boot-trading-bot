#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_USER="ayman01323"
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
MIGRATION_STATE="/var/lib/google-production-migration"
ACTIVATE="/usr/local/sbin/activate-google-production-bot"
STOP_FAILED="/usr/local/sbin/stop-google-production-after-failed-cutover"
STATUS="/usr/local/sbin/status-google-production-cutover"
SUDOERS="/etc/sudoers.d/google-production-cutover"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 2
fi
[[ "$(hostname)" == "botgoogle" ]] || { echo 'Refusing: expected botgoogle' >&2; exit 3; }
id "$RUNNER_USER" >/dev/null 2>&1 || { echo 'Google runner user missing' >&2; exit 4; }
[[ -d "$FINAL_DIR/.git" && -x "$FINAL_DIR/.venv/bin/python" ]] || { echo 'Prepared Google production path missing' >&2; exit 5; }
[[ -f "$MIGRATION_STATE/prepared" ]] || { echo 'Google migration prepared marker missing' >&2; exit 6; }

cat >"$ACTIVATE" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || exit 2
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
STATE="/var/lib/google-production-migration"
exec 9>/var/lock/google-production-cutover.lock
flock -w 60 9 || { echo 'Google cutover lock busy' >&2; exit 10; }
[[ "$(hostname)" == "botgoogle" ]] || exit 11
[[ -f "$STATE/prepared" ]] || { echo 'Prepared migration marker missing' >&2; exit 12; }
[[ -d "$FINAL_DIR/.git" && -x "$FINAL_DIR/.venv/bin/python" ]] || { echo 'Final production runtime missing' >&2; exit 13; }
if systemctl is-active --quiet "$SERVICE"; then
  echo 'Google learnerbot is already active; refusing duplicate activation' >&2
  exit 14
fi
[[ -f "$FINAL_DIR/.env" ]] || { echo 'Production .env missing on Google' >&2; exit 15; }
PYTHONPATH="$FINAL_DIR" "$FINAL_DIR/.venv/bin/python" -c 'import learnerbot' >/dev/null
UNIT_WD="$(systemctl show -p WorkingDirectory --value "$SERVICE" 2>/dev/null || true)"
[[ "$UNIT_WD" == "$FINAL_DIR" ]] || { echo "Service WorkingDirectory mismatch: $UNIT_WD" >&2; exit 16; }
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl start "$SERVICE"
for _ in $(seq 1 20); do
  systemctl is-active --quiet "$SERVICE" && break
  sleep 1
done
if ! systemctl is-active --quiet "$SERVICE"; then
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  systemctl disable "$SERVICE" >/dev/null 2>&1 || true
  echo 'Google learnerbot failed to become active; rolled back to stopped state' >&2
  exit 17
fi
sleep 5
if ! systemctl is-active --quiet "$SERVICE"; then
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  systemctl disable "$SERVICE" >/dev/null 2>&1 || true
  echo 'Google learnerbot did not remain active; rolled back to stopped state' >&2
  exit 18
fi
PID="$(systemctl show -p MainPID --value "$SERVICE")"
[[ "$PID" =~ ^[0-9]+$ && "$PID" -gt 1 ]] || { systemctl stop "$SERVICE"; systemctl disable "$SERVICE" >/dev/null 2>&1 || true; echo 'Invalid Google learnerbot PID' >&2; exit 19; }
CWD="$(readlink -f "/proc/$PID/cwd" 2>/dev/null || true)"
[[ "$CWD" == "$FINAL_DIR" ]] || { systemctl stop "$SERVICE"; systemctl disable "$SERVICE" >/dev/null 2>&1 || true; echo "Google learnerbot cwd mismatch: $CWD" >&2; exit 20; }
SHA="$(git -C "$FINAL_DIR" rev-parse HEAD)"
printf 'activated_at=%s\nsha=%s\npid=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$SHA" "$PID" > "$STATE/activated"
chmod 0600 "$STATE/activated"
echo 'GOOGLE_PRODUCTION_ACTIVE=true'
echo "google_sha=$SHA"
echo "google_pid=$PID"
echo 'google_service_active=true'
echo "google_service_enabled=$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
echo "google_process_cwd=$CWD"
EOF

cat >"$STOP_FAILED" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || exit 2
SERVICE="learnerbot.service"
STATE="/var/lib/google-production-migration"
systemctl stop "$SERVICE" >/dev/null 2>&1 || true
systemctl disable "$SERVICE" >/dev/null 2>&1 || true
rm -f "$STATE/activated"
echo 'GOOGLE_PRODUCTION_STOPPED_FOR_ROLLBACK=true'
EOF

cat >"$STATUS" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || exit 2
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
echo "google_service_active=$(systemctl is-active "$SERVICE" 2>/dev/null || true)"
echo "google_service_enabled=$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
echo "google_sha=$(git -C "$FINAL_DIR" rev-parse HEAD 2>/dev/null || true)"
PID="$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || true)"
echo "google_pid=$PID"
if [[ "$PID" =~ ^[0-9]+$ && "$PID" -gt 1 ]]; then echo "google_process_cwd=$(readlink -f "/proc/$PID/cwd" 2>/dev/null || true)"; fi
EOF

chmod 0755 "$ACTIVATE" "$STOP_FAILED" "$STATUS"
chown root:root "$ACTIVATE" "$STOP_FAILED" "$STATUS"
cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $ACTIVATE
$RUNNER_USER ALL=(root) NOPASSWD: $STOP_FAILED
$RUNNER_USER ALL=(root) NOPASSWD: $STATUS
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

echo 'Installed Google production activation bridge:'
echo "  $ACTIVATE"
echo "  $STOP_FAILED"
echo "  $STATUS"
echo 'No service was started by this installer.'

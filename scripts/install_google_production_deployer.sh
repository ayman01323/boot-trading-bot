#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_USER="ayman01323"
BOT_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
REPO_MATCH="ayman01323/boot-trading-bot"
DEPLOY="/usr/local/sbin/deploy-google-production-bot"
STATUS="/usr/local/sbin/status-google-production-bot"
RESTART="/usr/local/sbin/restart-google-production-bot"
SUDOERS="/etc/sudoers.d/google-production-deployer"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 2
fi
[[ "$(hostname)" == "botgoogle" ]] || { echo "Refusing: expected botgoogle" >&2; exit 3; }
id "$RUNNER_USER" >/dev/null 2>&1 || { echo "Missing runner user: $RUNNER_USER" >&2; exit 4; }
[[ -d "$BOT_DIR/.git" && -x "$BOT_DIR/.venv/bin/python" ]] || { echo "Google production checkout/venv missing" >&2; exit 5; }
ORIGIN="$(git -C "$BOT_DIR" remote get-url origin 2>/dev/null || true)"
[[ "$ORIGIN" == *"$REPO_MATCH"* ]] || { echo "Refusing: origin mismatch: $ORIGIN" >&2; exit 6; }

cat >"$DEPLOY" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
TARGET_SHA="${1:-}"
BOT_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
REPO_MATCH="ayman01323/boot-trading-bot"
LOG="/var/log/boot-google-production-deploy.log"

[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid deployment SHA" >&2; exit 2; }
exec 9>/var/lock/boot-google-production-deploy.lock
flock -w 60 9 || { echo "Another Google production deploy is running" >&2; exit 3; }
log() { printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG"; }
rollback() {
  local old="$1"
  log "ROLLBACK -> $old"
  git -C "$BOT_DIR" reset --hard "$old"
  systemctl restart "$SERVICE" || true
}

[[ "$(hostname)" == "botgoogle" ]] || { echo "Refusing: wrong host" >&2; exit 4; }
cd "$BOT_DIR"
ORIGIN="$(git remote get-url origin 2>/dev/null || true)"
[[ "$ORIGIN" == *"$REPO_MATCH"* ]] || { log "REFUSED origin mismatch: $ORIGIN"; exit 5; }

# Never overwrite tracked runtime/config changes silently. Untracked .env, CSV/data,
# wallet material and runtime files are preserved because git clean is never used.
if ! git diff --quiet || ! git diff --cached --quiet; then
  log "REFUSED tracked local changes exist"
  git status --short --untracked-files=no
  exit 6
fi

OLD_SHA="$(git rev-parse HEAD)"
log "BEGIN current=$OLD_SHA requested=$TARGET_SHA"
git fetch --prune origin main
REMOTE_SHA="$(git rev-parse origin/main)"
[[ "$REMOTE_SHA" == "$TARGET_SHA" ]] || { log "REFUSED requested SHA is not origin/main ($REMOTE_SHA)"; exit 7; }

if [[ "$OLD_SHA" != "$TARGET_SHA" ]]; then
  git checkout -q main
  git merge --ff-only "$TARGET_SHA" || { log "REFUSED non-fast-forward deployment"; exit 8; }
fi

PY="$BOT_DIR/.venv/bin/python"
if ! "$PY" -m pip install -q -r requirements.txt; then
  log "DEPENDENCY INSTALL FAILED"
  git reset --hard "$OLD_SHA"
  exit 9
fi
if ! "$PY" -m compileall -q learnerbot scripts; then
  log "PYTHON COMPILE FAILED"
  git reset --hard "$OLD_SHA"
  exit 10
fi
if ! "$PY" -m pytest -q; then
  log "TESTS FAILED; running process left untouched"
  git reset --hard "$OLD_SHA"
  exit 11
fi

log "TESTS PASSED; restarting Google learnerbot"
systemctl restart "$SERVICE"
for _ in $(seq 1 20); do
  systemctl is-active --quiet "$SERVICE" && break
  sleep 1
done
if ! systemctl is-active --quiet "$SERVICE"; then
  log "NEW VERSION FAILED TO START"
  rollback "$OLD_SHA"
  sleep 3
  systemctl is-active --quiet "$SERVICE" || { log "CRITICAL rollback service failed"; exit 13; }
  exit 12
fi
sleep 5
PID="$(systemctl show -p MainPID --value "$SERVICE")"
[[ "$PID" =~ ^[0-9]+$ && "$PID" -gt 1 ]] || { rollback "$OLD_SHA"; echo "Invalid learnerbot PID" >&2; exit 14; }
CWD="$(readlink -f "/proc/$PID/cwd" 2>/dev/null || true)"
[[ "$CWD" == "$BOT_DIR" ]] || { rollback "$OLD_SHA"; echo "learnerbot cwd mismatch: $CWD" >&2; exit 15; }
NEW_SHA="$(git rev-parse HEAD)"
VERSION="$($PY -c 'import learnerbot; print(getattr(learnerbot,"__version__","unknown"))' 2>/dev/null || true)"
log "SUCCESS sha=$NEW_SHA version=$VERSION pid=$PID cwd=$CWD"
echo "GOOGLE_DEPLOY_SUCCESS=true"
echo "google_sha=$NEW_SHA"
echo "google_pid=$PID"
echo "google_process_cwd=$CWD"
EOF

cat >"$STATUS" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
BOT_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
[[ "$(hostname)" == "botgoogle" ]] || exit 2
echo "google_service_active=$(systemctl is-active "$SERVICE" 2>/dev/null || true)"
echo "google_service_enabled=$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
echo "google_sha=$(git -C "$BOT_DIR" rev-parse HEAD 2>/dev/null || true)"
echo "google_tracked_dirty=$(git -C "$BOT_DIR" status --porcelain --untracked-files=no 2>/dev/null | wc -l)"
echo "google_version=$($BOT_DIR/.venv/bin/python -c 'import learnerbot; print(getattr(learnerbot,"__version__","unknown"))' 2>/dev/null || true)"
PID="$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || true)"
echo "google_pid=$PID"
if [[ "$PID" =~ ^[0-9]+$ && "$PID" -gt 1 ]]; then
  echo "google_process_cwd=$(readlink -f "/proc/$PID/cwd" 2>/dev/null || true)"
fi
EOF

cat >"$RESTART" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
BOT_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
[[ "$(hostname)" == "botgoogle" ]] || exit 2
systemctl restart "$SERVICE"
sleep 5
systemctl is-active --quiet "$SERVICE" || exit 3
PID="$(systemctl show -p MainPID --value "$SERVICE")"
[[ "$PID" =~ ^[0-9]+$ && "$PID" -gt 1 ]] || exit 4
CWD="$(readlink -f "/proc/$PID/cwd" 2>/dev/null || true)"
[[ "$CWD" == "$BOT_DIR" ]] || exit 5
echo "GOOGLE_RESTART_SUCCESS=true"
echo "google_pid=$PID"
echo "google_process_cwd=$CWD"
EOF

chmod 0755 "$DEPLOY" "$STATUS" "$RESTART"
chown root:root "$DEPLOY" "$STATUS" "$RESTART"
cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $DEPLOY *
$RUNNER_USER ALL=(root) NOPASSWD: $STATUS
$RUNNER_USER ALL=(root) NOPASSWD: $RESTART
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

echo "GOOGLE_PRODUCTION_DEPLOY_BRIDGE_INSTALLED=true"
echo "deploy=$DEPLOY"
echo "status=$STATUS"
echo "restart=$RESTART"
echo "target=$BOT_DIR"
echo "service=$SERVICE"

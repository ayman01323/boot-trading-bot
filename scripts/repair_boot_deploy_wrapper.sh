#!/usr/bin/env bash
set -Eeuo pipefail

# Repair only the missing root-owned deploy wrapper used by the existing
# self-hosted GitHub Actions runner. This does not install/re-register a runner,
# read .env/private keys, or change trading/LIVE settings.

BOT_DIR="${BOT_DIR:-/root/multichain-learning-bot-v2.2-fast-direct-market}"
SERVICE_NAME="${SERVICE_NAME:-learnerbot}"
REPO_MATCH="${REPO_MATCH:-ayman01323/boot-trading-bot}"
DEPLOY_WRAPPER="/usr/local/sbin/deploy-boot-trading-bot"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 2
fi

if [[ ! -d "$BOT_DIR/.git" ]]; then
  echo "Refusing repair: BOT_DIR is not a git checkout: $BOT_DIR" >&2
  exit 2
fi
if [[ ! -x "$BOT_DIR/.venv/bin/python" ]]; then
  echo "Refusing repair: expected virtualenv Python is missing: $BOT_DIR/.venv/bin/python" >&2
  exit 2
fi

origin="$(git -C "$BOT_DIR" remote get-url origin 2>/dev/null || true)"
if [[ "$origin" != *"$REPO_MATCH"* ]]; then
  echo "Refusing repair: origin does not match $REPO_MATCH" >&2
  echo "origin=$origin" >&2
  exit 3
fi

cat >"$DEPLOY_WRAPPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
TARGET_SHA="\${1:-}"
BOT_DIR="$BOT_DIR"
SERVICE="$SERVICE_NAME"
REPO_MATCH="$REPO_MATCH"
LOG="/var/log/boot-github-deploy.log"

if [[ ! "\$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid deployment SHA" >&2
  exit 2
fi

exec 9>/var/lock/boot-github-deploy.lock
if ! flock -w 60 9; then
  echo "Another deployment is already running" >&2
  exit 3
fi

log() {
  printf '%s %s\n' "\$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "\$*" | tee -a "\$LOG"
}
rollback() {
  local old="\$1"
  log "ROLLBACK -> \$old"
  git reset --hard "\$old"
  systemctl restart "\$SERVICE" || true
}

cd "\$BOT_DIR"
ORIGIN="\$(git remote get-url origin 2>/dev/null || true)"
if [[ "\$ORIGIN" != *"\$REPO_MATCH"* ]]; then
  log "REFUSED origin mismatch: \$ORIGIN"
  exit 4
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  log "REFUSED tracked local changes exist"
  git status --short --untracked-files=no
  exit 5
fi

git checkout -q main
OLD_SHA="\$(git rev-parse HEAD)"
log "BEGIN current=\$OLD_SHA requested=\$TARGET_SHA"

git fetch --prune origin main
REMOTE_SHA="\$(git rev-parse origin/main)"
if [[ "\$REMOTE_SHA" != "\$TARGET_SHA" ]]; then
  log "REFUSED requested SHA is not current origin/main (origin/main=\$REMOTE_SHA)"
  exit 6
fi

if [[ "\$OLD_SHA" == "\$TARGET_SHA" ]]; then
  log "ALREADY DEPLOYED \$TARGET_SHA"
  systemctl is-active "\$SERVICE"
  exit 0
fi

if ! git merge --ff-only "\$TARGET_SHA"; then
  log "REFUSED non-fast-forward deployment"
  exit 7
fi

PY="\$BOT_DIR/.venv/bin/python"
if ! "\$PY" -m pip install -q -r requirements.txt; then
  log "DEPENDENCY INSTALL FAILED"
  git reset --hard "\$OLD_SHA"
  exit 8
fi
if ! "\$PY" -m compileall -q learnerbot scripts; then
  log "PYTHON COMPILE FAILED"
  git reset --hard "\$OLD_SHA"
  exit 9
fi
if ! "\$PY" -m pytest -q; then
  log "TESTS FAILED; service remains on old running process"
  git reset --hard "\$OLD_SHA"
  exit 10
fi

log "TESTS PASSED; restarting \$SERVICE"
systemctl restart "\$SERVICE"
sleep 4
if ! systemctl is-active --quiet "\$SERVICE"; then
  log "NEW VERSION FAILED TO START"
  rollback "\$OLD_SHA"
  sleep 3
  if ! systemctl is-active --quiet "\$SERVICE"; then
    log "CRITICAL rollback service also failed"
    systemctl status "\$SERVICE" --no-pager -l || true
    exit 12
  fi
  exit 11
fi

NEW_SHA="\$(git rev-parse HEAD)"
VERSION="\$("\$PY" -c 'import learnerbot; print(getattr(learnerbot,"__version__","unknown"))' 2>/dev/null || true)"
log "SUCCESS sha=\$NEW_SHA version=\$VERSION service=active"
systemctl status "\$SERVICE" --no-pager -l | tail -n 18 || true
EOF

chmod 0755 "$DEPLOY_WRAPPER"
chown root:root "$DEPLOY_WRAPPER"

# The sudo rule already exists on the VPS; verify it still authorises only this
# fixed wrapper path rather than broadening github-runner privileges.
if ! sudo -l -U github-runner 2>/dev/null | grep -Fq '/usr/local/sbin/deploy-boot-trading-bot *'; then
  echo "WARNING: deploy wrapper restored, but the existing github-runner sudo rule was not found." >&2
  echo "Do not add broad sudo access. Repair the specific sudoers entry before deploying." >&2
  exit 4
fi

printf 'RESTORED %s\n' "$DEPLOY_WRAPPER"
ls -l "$DEPLOY_WRAPPER"
echo 'No Telegram token, wallet key, .env or LIVE setting was read or changed.'

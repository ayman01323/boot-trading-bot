#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/multichain-learning-bot-v2.2-fast-direct-market"
BRANCH="challenge-auto"
REMOTE="origin"
LOG="/root/boot-auto-deploy.log"
STATE="/root/boot-auto-deploy.last"
LOCK="/root/boot-auto-deploy.lock"
TIMER_MIGRATION_MARKER="/root/boot-auto-deploy.timer-v20.initialized"

exec 9>"$LOCK"
flock -n 9 || exit 0

cd "$ROOT"

log(){ printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
notify(){
  if [[ -f scripts/telegram_deploy_notify.py ]]; then
    ./.venv/bin/python scripts/telegram_deploy_notify.py "$@" >>"$LOG" 2>&1 || true
  fi
}

# Never deploy with uncommitted tracked-code changes. Runtime/untracked data is ignored by Git.
if ! git diff --quiet || ! git diff --cached --quiet; then
  log "SKIP: local tracked changes present"
  notify FAILED "Automatic update skipped because local tracked-code changes are present."
  exit 0
fi

git fetch "$REMOTE" "$BRANCH" --quiet
TARGET="$(git rev-parse "$REMOTE/$BRANCH")"
CURRENT="$(git rev-parse HEAD)"
LAST="$(cat "$STATE" 2>/dev/null || true)"

if [[ "$TARGET" == "$CURRENT" || "$TARGET" == "$LAST" ]]; then
  exit 0
fi

BACKUP="auto-deploy-backup-$(date +%Y%m%d-%H%M%S)-${CURRENT:0:8}"
log "NEW: $TARGET (current $CURRENT); backup tag $BACKUP"
notify STARTED "New challenge-auto code detected: ${TARGET:0:12}. Validating before live restart."
git tag "$BACKUP" "$CURRENT"

# Update code to exact approved challenge branch commit.
git checkout -q "$BRANCH" 2>/dev/null || git checkout -q -b "$BRANCH" "$REMOTE/$BRANCH"
git reset --hard "$TARGET" >/dev/null

# Validation: syntax + targeted tests. Do not alter live CSV/data.
if ! ./.venv/bin/python -m compileall -q learnerbot scripts; then
  log "FAIL compile; rollback to $CURRENT"
  notify ROLLBACK "Compile validation failed for ${TARGET:0:12}; restoring ${CURRENT:0:12}."
  git reset --hard "$CURRENT" >/dev/null
  systemctl restart learnerbot || true
  exit 1
fi

# Run the fastest meaningful regression set if present; otherwise all tests.
TESTS=()
for f in tests/test_v231_v3_router_deadline.py tests/test_v23_full_power.py tests/test_v233_dynamic_products.py; do
  [[ -f "$f" ]] && TESTS+=("$f")
done
if [[ ${#TESTS[@]} -gt 0 ]]; then
  if ! ./.venv/bin/python -m pytest -q "${TESTS[@]}"; then
    log "FAIL tests; rollback to $CURRENT"
    notify ROLLBACK "Regression tests failed for ${TARGET:0:12}; restoring ${CURRENT:0:12}."
    git reset --hard "$CURRENT" >/dev/null
    systemctl restart learnerbot || true
    exit 1
  fi
else
  if ! ./.venv/bin/python -m pytest -q; then
    log "FAIL tests; rollback to $CURRENT"
    notify ROLLBACK "Test suite failed for ${TARGET:0:12}; restoring ${CURRENT:0:12}."
    git reset --hard "$CURRENT" >/dev/null
    systemctl restart learnerbot || true
    exit 1
  fi
fi

systemctl restart learnerbot
sleep 5
if ! systemctl is-active --quiet learnerbot; then
  log "FAIL service restart; rollback to $CURRENT"
  notify ROLLBACK "learnerbot did not become active after ${TARGET:0:12}; restoring ${CURRENT:0:12}."
  git reset --hard "$CURRENT" >/dev/null
  systemctl restart learnerbot || true
  exit 1
fi

# One-time migration from the original one-minute timer to 20 seconds.
# Future MASTER Telegram changes persist because this block runs only once.
if [[ ! -f "$TIMER_MIGRATION_MARKER" ]]; then
  if ./.venv/bin/python -c 'from learnerbot.deploy_timer import ensure_default_20_seconds; print(ensure_default_20_seconds())' >>"$LOG" 2>&1; then
    touch "$TIMER_MIGRATION_MARKER"
    log "AUTO-DEPLOY TIMER: initial interval set to 20 seconds"
    notify DEPLOYED "GitHub auto-deploy check interval is now 20 seconds. MASTER can change it from Telegram → ⏱ Auto-Deploy Timer."
  else
    log "WARN: could not migrate auto-deploy timer to 20 seconds"
    notify FAILED "Code deployed, but changing the auto-deploy timer to 20 seconds failed. Existing timer remains in effect."
  fi
fi

printf '%s' "$TARGET" > "$STATE"
log "DEPLOYED: $TARGET; learnerbot active"
notify DEPLOYED "Commit ${TARGET:0:12} passed validation and learnerbot is active."

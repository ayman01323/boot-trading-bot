#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/multichain-learning-bot-v2.2-fast-direct-market"
BRANCH="challenge-auto"
REMOTE="origin"
LOG="/root/boot-auto-deploy.log"
STATE="/root/boot-auto-deploy.last"
LOCK="/root/boot-auto-deploy.lock"
TIMER_MIGRATION_MARKER="/root/boot-auto-deploy.timer-v5.initialized"
PROFIT_CHALLENGE_MARKER="/root/boot-profit-challenge-5h-target001.started"
PROFIT_CHALLENGE_UNIT="boot-profit-challenge.service"

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

# Start the operator-requested bounded live profit challenge exactly once.
# The challenge does not raise capital/slippage or bypass final simulation/profit checks.
if [[ ! -f "$PROFIT_CHALLENGE_MARKER" ]]; then
  if systemctl is-active --quiet "$PROFIT_CHALLENGE_UNIT"; then
    touch "$PROFIT_CHALLENGE_MARKER"
    log "PROFIT CHALLENGE: already active; marker recorded"
  else
    systemctl reset-failed "$PROFIT_CHALLENGE_UNIT" 2>/dev/null || true
    if systemd-run --quiet --unit=boot-profit-challenge \
      --property=Type=simple \
      --property="WorkingDirectory=$ROOT" \
      "$ROOT/.venv/bin/python" "$ROOT/scripts/profit_challenge.py" \
      --hours 5 --target-usd 0.01 --report-minutes 15; then
      sleep 2
      if systemctl is-active --quiet "$PROFIT_CHALLENGE_UNIT"; then
        touch "$PROFIT_CHALLENGE_MARKER"
        log "PROFIT CHALLENGE: started 5h target=0.01 USD"
        notify STARTED "BOOT 5-hour profit challenge started. Target: at least $0.01 realised user net. Goal alert is enabled. Profit is not guaranteed and safety checks remain active."
      else
        log "WARN: profit challenge unit did not stay active"
        notify FAILED "BOOT profit challenge could not stay active after startup. No safeguards were bypassed."
      fi
    else
      log "WARN: systemd-run could not start profit challenge"
      notify FAILED "BOOT profit challenge could not be started automatically."
    fi
  fi
fi

# Attempt the requested one-shot Telegram delivery test directly from the deploy
# path as well as learnerbot startup. The data marker prevents duplicate success.
if [[ -f learnerbot/challenge_alerts.py ]]; then
  if ./.venv/bin/python -c 'from learnerbot.config import AppSettings; from learnerbot.challenge_alerts import send_target_test_once; print(send_target_test_once(AppSettings.load()))' >>"$LOG" 2>&1; then
    log "TELEGRAM TEST: delivery attempt completed"
  else
    log "WARN: Telegram delivery test command failed"
    notify FAILED "Code deployed, but the Telegram delivery test command failed. Check BOOT Telegram configuration."
  fi
fi

# One-time migration to the requested 5-second GitHub poll interval.
# Future MASTER Telegram changes persist because this marker runs only once.
if [[ ! -f "$TIMER_MIGRATION_MARKER" ]]; then
  if ./.venv/bin/python -c 'from learnerbot.deploy_timer import ensure_default_5_seconds; print(ensure_default_5_seconds())' >>"$LOG" 2>&1; then
    touch "$TIMER_MIGRATION_MARKER"
    log "AUTO-DEPLOY TIMER: initial interval set to 5 seconds"
    notify DEPLOYED "GitHub auto-deploy check interval is now 5 seconds. MASTER can change it from Telegram → ⏱ Auto-Deploy Timer."
  else
    log "WARN: could not migrate auto-deploy timer to 5 seconds"
    notify FAILED "Code deployed, but changing the auto-deploy timer to 5 seconds failed. Existing timer remains in effect."
  fi
fi

printf '%s' "$TARGET" > "$STATE"
log "DEPLOYED: $TARGET; learnerbot active"
notify DEPLOYED "Commit ${TARGET:0:12} passed validation and learnerbot is active."

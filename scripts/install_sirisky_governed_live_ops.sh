#!/usr/bin/env bash
set -Eeuo pipefail

# One-time bootstrap for controlled GitHub Actions -> SiRisky governed LIVE arming.
# The runner receives passwordless sudo for ONE root-owned wrapper only.
# The wrapper is kept under the root-owned SiRisky bot directory, not /usr/local/sbin.
# Set ARM_NOW=1 when invoking this installer to apply that policy immediately
# to the exact commit currently checked out by the self-hosted runner.

RUNNER_USER="${RUNNER_USER:-ayman01323}"
RUNNER_CHECKOUT="${RUNNER_CHECKOUT:-/home/ayman01323/gh-runner/botgoogle/boot-trading-bot/boot-trading-bot}"
SIRISKY_DIR="${SIRISKY_DIR:-/root/SiRisky}"
SERVICE_NAME="${SERVICE_NAME:-sirisky.service}"
WRAPPER="$SIRISKY_DIR/ops/sirisky-governed-live-arm"
SUDOERS="/etc/sudoers.d/sirisky-governed-live"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 2
fi

if [[ "$(hostname)" != "botgoogle" ]]; then
  echo "Refusing install: expected hostname botgoogle" >&2
  exit 2
fi
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  echo "Runner user does not exist: $RUNNER_USER" >&2
  exit 2
fi
if [[ ! -d "$RUNNER_CHECKOUT/.git" ]]; then
  echo "Runner checkout not found: $RUNNER_CHECKOUT" >&2
  exit 2
fi
if [[ ! -d "$SIRISKY_DIR" || ! -x "$SIRISKY_DIR/.venv/bin/python" ]]; then
  echo "SiRisky runtime not found: $SIRISKY_DIR" >&2
  exit 2
fi

mkdir -p "$SIRISKY_DIR/ops"
chown root:root "$SIRISKY_DIR/ops"
chmod 0755 "$SIRISKY_DIR/ops"

cat >"$WRAPPER" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_SHA="${1:-}"
SRC="/home/ayman01323/gh-runner/botgoogle/boot-trading-bot/boot-trading-bot"
DST="/root/SiRisky"
SERVICE="sirisky.service"

if [[ "$(hostname)" != "botgoogle" ]]; then
  echo "REFUSED: wrong host" >&2
  exit 2
fi
if [[ ! "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "REFUSED: invalid commit SHA" >&2
  exit 2
fi
if [[ ! -d "$SRC/.git" || ! -d "$DST" || ! -x "$DST/.venv/bin/python" ]]; then
  echo "REFUSED: required checkout/runtime missing" >&2
  exit 2
fi

cd "$SRC"
ACTUAL_SHA="$(git rev-parse HEAD)"
if [[ "$ACTUAL_SHA" != "$TARGET_SHA" ]]; then
  echo "REFUSED: runner checkout SHA $ACTUAL_SHA does not match requested $TARGET_SHA" >&2
  exit 3
fi

RUNTIME="$DST/CSV/runtime.csv"
[[ -f "$RUNTIME" ]] || { echo "REFUSED: runtime.csv missing" >&2; exit 4; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$DST/data/live-armed-runtime-backup-$stamp.csv"
cp -a "$RUNTIME" "$BACKUP"

rollback() {
  rc=$?
  if (( rc != 0 )); then
    echo "ROLLBACK: restoring $BACKUP" >&2
    cp -a "$BACKUP" "$RUNTIME" || true
    systemctl restart "$SERVICE" || true
  fi
  exit $rc
}
trap rollback EXIT

python3 - "$RUNTIME" <<'PY'
import csv, sys
from pathlib import Path

p=Path(sys.argv[1])
rows=list(csv.DictReader(p.open(encoding='utf-8-sig',newline='')))
by={str(r.get('setting') or ''):r for r in rows}
updates={
    'trading_mode':('LIVE','Governed LIVE runtime; manual/external signature gate retained'),
    'paper_auto_trade_enabled':('0','Paper auto execution disabled in governed LIVE'),
    'live_enabled':('1','Live market data and governed execution path enabled'),
    'broadcast_enabled':('1','Broadcast path armed; external/manual signature policy remains mandatory'),
    'auto_discovery_enabled':('1','Continuously discover fresh Solana pools'),
    'auto_promote_to_selected':('1','Automatically feed ranked candidates into Stage 2/3 evaluation'),
    'auto_evaluate_candidate_limit':('5','Evaluate up to five ranked candidates per cycle'),
    'manual_approval_enabled':('1','Every Stage-3-approved BUY/SELL stops at manual approval'),
    'armed_manual_approval':('1','Governed LIVE is armed behind manual approval'),
    'manual_approval_ttl_seconds':('60','Each immutable trade proposal expires after 60 seconds'),
    'manual_approval_require_external_signature':('1','Final signing remains external/manual; server wallet may not bypass this gate'),
    'telegram_manual_run_enabled':('0','Telegram cannot directly execute a real transaction'),
    'poll_seconds':('5','Engine polling interval'),
}
for key,(value,note) in updates.items():
    if key in by:
        by[key]['value']=value
        by[key]['notes']=note
    else:
        row={'setting':key,'value':value,'notes':note}
        rows.append(row)
        by[key]=row

tmp=p.with_suffix('.csv.tmp')
with tmp.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['setting','value','notes'])
    w.writeheader(); w.writerows(rows)
tmp.replace(p)
PY

runtime_value() {
  awk -F, -v key="$1" '$1==key {gsub(/\r/,"",$2); print $2}' "$RUNTIME"
}

[[ "$(runtime_value trading_mode)" == "LIVE" ]]
[[ "$(runtime_value live_enabled)" == "1" ]]
[[ "$(runtime_value broadcast_enabled)" == "1" ]]
[[ "$(runtime_value manual_approval_enabled)" == "1" ]]
[[ "$(runtime_value armed_manual_approval)" == "1" ]]
[[ "$(runtime_value manual_approval_require_external_signature)" == "1" ]]
[[ "$(runtime_value telegram_manual_run_enabled)" == "0" ]]

cd "$DST"
PYTHONPATH=. .venv/bin/python -m compileall -q sirisky run.py tests
PYTHONPATH=. .venv/bin/python run.py selftest

systemctl restart "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE"

trap - EXIT

echo "=== SIRISKY GOVERNED LIVE ARMED ==="
echo "sha=$TARGET_SHA"
echo "service=$(systemctl is-active "$SERVICE")"
echo "trading_mode=$(runtime_value trading_mode)"
echo "live_enabled=$(runtime_value live_enabled)"
echo "broadcast_enabled=$(runtime_value broadcast_enabled)"
echo "manual_approval_enabled=$(runtime_value manual_approval_enabled)"
echo "manual_approval_require_external_signature=$(runtime_value manual_approval_require_external_signature)"
echo "telegram_manual_run_enabled=$(runtime_value telegram_manual_run_enabled)"
echo "rollback_snapshot=$BACKUP"
echo "NOTE: this state is LIVE-ready but still stops at WAITING_FOR_MANUAL_APPROVAL; it does not create an autonomous server-signing bypass."
EOF

chmod 0755 "$WRAPPER"
chown root:root "$WRAPPER"

cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $WRAPPER *
EOF
chmod 0440 "$SUDOERS"
chown root:root "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

# Prove the exact runner identity can invoke the root-owned bot-local wrapper
# without granting arbitrary sudo.
sudo -u "$RUNNER_USER" sudo -n -l "$WRAPPER" >/dev/null

echo "SiRisky governed LIVE GitHub operation installed."
echo "Runner user: $RUNNER_USER"
echo "Allowed root command: $WRAPPER <40-char-git-sha>"
echo "Arbitrary passwordless sudo was NOT granted."
echo "Manual approval and external-signature requirements remain enabled."

if [[ "${ARM_NOW:-0}" == "1" ]]; then
  TARGET_SHA="$(git -C "$RUNNER_CHECKOUT" rev-parse HEAD)"
  echo "Applying governed LIVE policy to current runner checkout: $TARGET_SHA"
  "$WRAPPER" "$TARGET_SHA"
fi

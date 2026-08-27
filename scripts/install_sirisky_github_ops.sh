#!/usr/bin/env bash
set -Eeuo pipefail

# One-time bootstrap for controlled GitHub Actions -> SiRisky operations.
# The runner receives passwordless sudo for ONE root-owned wrapper only.
# The wrapper can deploy only the fixed SiRisky SHADOW files below and always
# forces broadcast_enabled=0 plus the external/manual-signature live boundary.

RUNNER_USER="${RUNNER_USER:-ayman01323}"
RUNNER_CHECKOUT="${RUNNER_CHECKOUT:-/home/ayman01323/gh-runner/botgoogle/boot-trading-bot/boot-trading-bot}"
SIRISKY_DIR="${SIRISKY_DIR:-/root/SiRisky}"
SERVICE_NAME="${SERVICE_NAME:-sirisky.service}"
WRAPPER="/usr/local/sbin/sirisky-shadow-deploy"
SUDOERS="/etc/sudoers.d/sirisky-github-runner"

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

FILES=(
  "SiRisky/overrides/run.py"
  "SiRisky/overrides/sirisky/engine.py"
  "SiRisky/overrides/sirisky/stage1_data.py"
  "SiRisky/overrides/sirisky/stage3_risk.py"
  "SiRisky/overrides/sirisky/stage5_trade.py"
)
for f in "${FILES[@]}"; do
  [[ -f "$SRC/$f" ]] || { echo "REFUSED: missing source $f" >&2; exit 4; }
done

# Refuse any source revision that removes the hard SHADOW/broadcast locks from
# the deployment workflow inputs we rely on.
grep -q 'broadcast_enabled' "$SRC/SiRisky/overrides/run.py"
grep -q 'mode=="SHADOW"' "$SRC/SiRisky/overrides/sirisky/stage5_trade.py"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$DST/data/deploy_backups/$stamp"
mkdir -p "$BACKUP/sirisky"
cp -a "$DST/run.py" "$BACKUP/run.py"
for f in engine.py stage1_data.py stage3_risk.py stage5_trade.py; do
  cp -a "$DST/sirisky/$f" "$BACKUP/sirisky/$f"
done

rollback() {
  echo "ROLLBACK: restoring $BACKUP" >&2
  cp -a "$BACKUP/run.py" "$DST/run.py" || true
  for f in engine.py stage1_data.py stage3_risk.py stage5_trade.py; do
    cp -a "$BACKUP/sirisky/$f" "$DST/sirisky/$f" || true
  done
  systemctl restart "$SERVICE" || true
}
trap 'rc=$?; if (( rc != 0 )); then rollback; fi; exit $rc' EXIT

install -m 0644 "$SRC/SiRisky/overrides/run.py" "$DST/run.py"
install -m 0644 "$SRC/SiRisky/overrides/sirisky/engine.py" "$DST/sirisky/engine.py"
install -m 0644 "$SRC/SiRisky/overrides/sirisky/stage1_data.py" "$DST/sirisky/stage1_data.py"
install -m 0644 "$SRC/SiRisky/overrides/sirisky/stage3_risk.py" "$DST/sirisky/stage3_risk.py"
install -m 0644 "$SRC/SiRisky/overrides/sirisky/stage5_trade.py" "$DST/sirisky/stage5_trade.py"

python3 - "$DST/CSV/runtime.csv" <<'PY'
import csv, sys
from pathlib import Path
p=Path(sys.argv[1])
rows=list(csv.DictReader(p.open(encoding='utf-8-sig',newline='')))
by={str(r.get('setting') or ''):r for r in rows}
updates={
  'trading_mode':('SHADOW','Automatic Stage 1-8 paper/shadow lifecycle; no real broadcast'),
  'paper_auto_trade_enabled':('1','Automatic Stage 1-8 SHADOW lifecycle'),
  'live_enabled':('1','Live market data while execution remains SHADOW'),
  'auto_discovery_enabled':('1','Continuously discover fresh Solana pools'),
  'auto_promote_to_selected':('1','Feed ranked Stage-1 candidates automatically'),
  'auto_evaluate_candidate_limit':('5','Evaluate up to five distinct ranked candidates per cycle'),
  'failed_mint_cooldown_seconds':('180','Skip failed SHADOW BUY mint for three minutes'),
  'manual_approval_enabled':('1','Retained for any non-SHADOW/live-money path'),
  'manual_approval_require_external_signature':('1','Retained for any non-SHADOW/live-money path'),
  'broadcast_enabled':('0','Hard lock: no real transaction broadcast'),
  'telegram_manual_run_enabled':('0','Telegram cannot directly execute a real transaction'),
}
for k,(v,note) in updates.items():
    if k in by:
        by[k]['value']=v; by[k]['notes']=note
    else:
        row={'setting':k,'value':v,'notes':note}; rows.append(row); by[k]=row
with p.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['setting','value','notes'])
    w.writeheader(); w.writerows(rows)
PY

runtime_value() {
  awk -F, -v key="$1" '$1==key {gsub(/\r/,"",$2); print $2}' "$DST/CSV/runtime.csv"
}
[[ "$(runtime_value trading_mode)" == "SHADOW" ]]
[[ "$(runtime_value paper_auto_trade_enabled)" == "1" ]]
[[ "$(runtime_value broadcast_enabled)" == "0" ]]
[[ "$(runtime_value manual_approval_require_external_signature)" == "1" ]]

cd "$DST"
PYTHONPATH=. .venv/bin/python -m compileall -q sirisky run.py tests
PYTHONPATH=. .venv/bin/python run.py selftest
PYTHONPATH=. .venv/bin/python run.py check

systemctl restart "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE"

# A single validation cycle is safe because the wrapper has forced SHADOW and
# broadcast=0 immediately beforehand.
PYTHONPATH=. .venv/bin/python run.py once

[[ "$(runtime_value trading_mode)" == "SHADOW" ]]
[[ "$(runtime_value broadcast_enabled)" == "0" ]]

trap - EXIT

echo "=== SIRISKY SHADOW DEPLOY OK ==="
echo "sha=$TARGET_SHA"
echo "service=$(systemctl is-active "$SERVICE")"
echo "trading_mode=$(runtime_value trading_mode)"
echo "broadcast_enabled=$(runtime_value broadcast_enabled)"
echo "failed_mint_cooldown_seconds=$(runtime_value failed_mint_cooldown_seconds)"
echo "discovery_state=$(cat "$DST/data/stage1_discovery_state.json" 2>/dev/null || echo '{}')"
echo "latest_executions:"
tail -n 8 "$DST/CSV/executions.csv" 2>/dev/null || true
EOF

chmod 0755 "$WRAPPER"
chown root:root "$WRAPPER"

cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $WRAPPER *
EOF
chmod 0440 "$SUDOERS"
chown root:root "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

# Prove the exact runner identity can invoke the wrapper without a password.
sudo -u "$RUNNER_USER" sudo -n -l "$WRAPPER" >/dev/null

echo "SiRisky GitHub operations installed."
echo "Runner user: $RUNNER_USER"
echo "Allowed root command: $WRAPPER <40-char-git-sha>"
echo "Arbitrary passwordless sudo was NOT granted."

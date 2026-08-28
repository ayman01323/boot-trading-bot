#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

WORK="$(mktemp -d /tmp/sirisky-jupiter-governor.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

git clone --depth 1 --branch main https://github.com/ayman01323/boot-trading-bot.git "$WORK/repo"
SRC="$WORK/repo/SiRisky/overrides/sirisky"
TARGET_SHA="$(git -C "$WORK/repo" rev-parse HEAD)"

cat > "$SRC/jupiter_governor.py" <<'PY'
from __future__ import annotations

import fcntl
import random
import time

import requests


def _runtime_float(settings, key: str, default: float) -> float:
    try:
        return float(settings.runtime().get(key) or default)
    except Exception:
        return float(default)


def _runtime_int(settings, key: str, default: int) -> int:
    try:
        return int(float(settings.runtime().get(key) or default))
    except Exception:
        return int(default)


def _retry_after_seconds(exc) -> float:
    response = getattr(exc, "response", None)
    if response is None:
        return 0.0
    try:
        raw = response.headers.get("Retry-After")
        return max(0.0, float(raw)) if raw else 0.0
    except Exception:
        return 0.0


def jupiter_call(settings, fn, *args, retries=None, **kwargs):
    """Serialize Jupiter calls across SiRisky and retry HTTP 429 safely."""
    spacing = max(0.25, min(10.0, _runtime_float(settings, "jupiter_min_interval_seconds", 2.25)))
    max_retries = _runtime_int(settings, "jupiter_max_retries", 4) if retries is None else int(retries)
    max_retries = max(0, min(8, max_retries))
    gate = settings.data_dir / ".jupiter-rate-gate"
    gate.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries + 1):
        with gate.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            try:
                last = float((handle.read() or "0").strip())
            except Exception:
                last = 0.0
            wait = spacing - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            started = time.time()
            handle.seek(0)
            handle.truncate()
            handle.write(f"{started:.6f}")
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        try:
            return fn(*args, **kwargs)
        except requests.exceptions.HTTPError as exc:
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) != 429:
                raise
            if attempt >= max_retries:
                raise RuntimeError("JUPITER_RATE_LIMITED") from None
            server_wait = _retry_after_seconds(exc)
            backoff = min(30.0, 2.0 * (2 ** attempt))
            time.sleep(max(server_wait, backoff) + random.uniform(0.05, 0.25))

    raise RuntimeError("JUPITER_RATE_LIMITED")
PY

python3 - "$SRC" <<'PY'
from pathlib import Path
import sys

root=Path(sys.argv[1])

def patch(path, replacements):
    p=root/path
    s=p.read_text(encoding="utf-8")
    for old,new in replacements:
        if old not in s:
            raise SystemExit(f"missing expected source in {path}: {old[:100]}")
        s=s.replace(old,new,1)
    p.write_text(s,encoding="utf-8")

patch("stage1_data.py", [
    ("from .jupiter import quote_only, WSOL_MINT, USDC_MINT\n",
     "from .jupiter import quote_only, WSOL_MINT, USDC_MINT\nfrom .jupiter_governor import jupiter_call\n"),
    ("buy = quote_only(self.settings, taker, WSOL_MINT, mint, lamports)",
     "buy = jupiter_call(self.settings, quote_only, self.settings, taker, WSOL_MINT, mint, lamports)"),
    ("sell = quote_only(self.settings, taker, mint, WSOL_MINT, out)",
     "sell = jupiter_call(self.settings, quote_only, self.settings, taker, mint, WSOL_MINT, out)"),
])

patch("stage5_trade.py", [
    ("from .jupiter import order as jup_order, execute_order, quote_only, WSOL_MINT\n",
     "from .jupiter import order as jup_order, execute_order, quote_only, WSOL_MINT\nfrom .jupiter_governor import jupiter_call\n"),
    ("q=quote_only(self.settings,taker,input_mint,output_mint,order.amount_raw)",
     "q=jupiter_call(self.settings,quote_only,self.settings,taker,input_mint,output_mint,order.amount_raw)"),
    ("q=jup_order(self.settings,taker,input_mint,output_mint,order.amount_raw)",
     "q=jupiter_call(self.settings,jup_order,self.settings,taker,input_mint,output_mint,order.amount_raw)"),
    ("res=execute_order(self.settings,q,wallet.keypair_bytes()); sig=str(res.get(\"signature\") or \"\"); status=\"SUCCESS\"",
     "res=jupiter_call(self.settings,execute_order,self.settings,q,wallet.keypair_bytes(),retries=0); sig=str(res.get(\"signature\") or \"\"); status=\"SUCCESS\""),
])

patch("stage6_monitor.py", [
    ("from .jupiter import quote_only, WSOL_MINT, token_balance_raw\n",
     "from .jupiter import quote_only, WSOL_MINT, token_balance_raw\nfrom .jupiter_governor import jupiter_call\n"),
    ("q = quote_only(self.settings, address, mint, WSOL_MINT, token_raw)",
     "q = jupiter_call(self.settings, quote_only, self.settings, address, mint, WSOL_MINT, token_raw)"),
])

patch("engine.py", [
    ('"stage3_passed":None,"error":type(exc).__name__,"discovery":discovery}',
     '"stage3_passed":None,"error":("JUPITER_RATE_LIMITED" if str(exc).strip()=="JUPITER_RATE_LIMITED" else type(exc).__name__),"discovery":discovery}'),
])
PY

python3 -m py_compile \
  "$SRC/jupiter_governor.py" \
  "$SRC/stage1_data.py" \
  "$SRC/stage5_trade.py" \
  "$SRC/stage6_monitor.py" \
  "$SRC/engine.py"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="/root/SiRisky/data/jupiter-governor-backup-$stamp"
sudo -n mkdir -p "$backup"
for f in stage1_data.py stage5_trade.py stage6_monitor.py engine.py; do
  sudo -n cp -a "/root/SiRisky/sirisky/$f" "$backup/$f"
done
if sudo -n test -f /root/SiRisky/sirisky/jupiter_governor.py; then
  sudo -n cp -a /root/SiRisky/sirisky/jupiter_governor.py "$backup/jupiter_governor.py"
fi

sudo -n install -m 0644 "$SRC/jupiter_governor.py" /root/SiRisky/sirisky/jupiter_governor.py
sudo -n install -m 0644 "$SRC/stage1_data.py" /root/SiRisky/sirisky/stage1_data.py
sudo -n install -m 0644 "$SRC/stage5_trade.py" /root/SiRisky/sirisky/stage5_trade.py
sudo -n install -m 0644 "$SRC/stage6_monitor.py" /root/SiRisky/sirisky/stage6_monitor.py
sudo -n install -m 0644 "$SRC/engine.py" /root/SiRisky/sirisky/engine.py

sudo -n python3 - /root/SiRisky/CSV/runtime.csv <<'PY'
import csv,sys
from pathlib import Path
p=Path(sys.argv[1])
rows=list(csv.DictReader(p.open(encoding="utf-8-sig",newline="")))
by={str(r.get("setting") or ""):r for r in rows}
updates={
    "auto_evaluate_candidate_limit":("1","Rate-safe LIVE candidate evaluation: one candidate per cycle"),
    "jupiter_min_interval_seconds":("2.25","Shared cross-process Jupiter call spacing"),
    "jupiter_max_retries":("4","HTTP 429 Retry-After/exponential backoff retries"),
}
for k,(v,n) in updates.items():
    if k in by:
        by[k]["value"]=v; by[k]["notes"]=n
    else:
        row={"setting":k,"value":v,"notes":n}; rows.append(row); by[k]=row
tmp=p.with_suffix(".csv.tmp")
with tmp.open("w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=["setting","value","notes"])
    w.writeheader(); w.writerows(rows)
tmp.replace(p)
PY

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky
PYTHONPATH=. .venv/bin/python -m compileall -q sirisky run.py tests
PYTHONPATH=. .venv/bin/python run.py selftest
systemctl restart sirisky.service
sleep 3
systemctl is-active --quiet sirisky.service
value() { awk -F, -v key="$1" '\''$1==key {gsub(/\r/,"",$2); print $2}'\'' CSV/runtime.csv; }
test "$(value trading_mode)" = LIVE
test "$(value live_enabled)" = 1
test "$(value broadcast_enabled)" = 1
test "$(value manual_approval_enabled)" = 0
test "$(value manual_approval_require_external_signature)" = 0
test "$(value auto_evaluate_candidate_limit)" = 1
test "$(value jupiter_min_interval_seconds)" = 2.25
test "$(value jupiter_max_retries)" = 4
grep -q jupiter_call sirisky/stage1_data.py
grep -q jupiter_call sirisky/stage5_trade.py
grep -q jupiter_call sirisky/stage6_monitor.py
echo "=== SIRISKY JUPITER GOVERNOR DEPLOYED ==="
echo "host=$(hostname)"
echo "service=$(systemctl is-active sirisky.service)"
echo "trading_mode=$(value trading_mode)"
echo "live_enabled=$(value live_enabled)"
echo "broadcast_enabled=$(value broadcast_enabled)"
echo "manual_approval_enabled=$(value manual_approval_enabled)"
echo "auto_evaluate_candidate_limit=$(value auto_evaluate_candidate_limit)"
echo "jupiter_min_interval_seconds=$(value jupiter_min_interval_seconds)"
echo "jupiter_max_retries=$(value jupiter_max_retries)"
'

echo "source_sha=$TARGET_SHA"
echo "backup=$backup"

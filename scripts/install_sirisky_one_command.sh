#!/usr/bin/env bash
set -euo pipefail

# One-command SAFE-OFF installer for SiRisky on the new Google server.
# Installs only to /root/SiRisky, bootstraps compatible existing runtime values,
# runs local tests and a non-broadcast preflight, and leaves LIVE/BROADCAST disabled.

TARGET=/root/SiRisky
SOURCE_ROOT=/root/multichain-learning-bot-v2.2-fast-direct-market
REPO=ayman01323/boot-trading-bot
BUNDLE_REF=fb2f47e5e6ed2f10f9a4a4bf112d49693dd735b1
TMP="$(mktemp -d /tmp/sirisky-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo 'ERROR: run this installer through sudo.' >&2
  exit 2
fi

[[ "$(hostname)" == "botgoogle" ]] || { echo 'ERROR: expected host botgoogle' >&2; exit 3; }
[[ -d "$SOURCE_ROOT" ]] || { echo "ERROR: existing production source not found at $SOURCE_ROOT" >&2; exit 4; }

echo '[1/8] Downloading pinned SiRisky bundle...'
mkdir -p "$TMP/parts"
for n in 01 02 03 04 05 06 07 08; do
  curl -fsSL "https://raw.githubusercontent.com/${REPO}/${BUNDLE_REF}/SiRisky/bundle_parts/part${n}.b64" -o "$TMP/parts/part${n}.b64"
done
cat "$TMP"/parts/part*.b64 | base64 -d > "$TMP/SiRisky.zip"

python3 - "$TMP/SiRisky.zip" "$TMP" <<'PY'
import sys, zipfile
from pathlib import Path
z=Path(sys.argv[1]); dest=Path(sys.argv[2]).resolve()
with zipfile.ZipFile(z) as f:
    for i in f.infolist():
        p=(dest/i.filename).resolve()
        if not (p == dest or str(p).startswith(str(dest) + '/')):
            raise SystemExit('unsafe archive path')
    f.extractall(dest)
PY
STAGE="$TMP/SiRisky"
[[ -f "$STAGE/run.py" ]] || { echo 'ERROR: bundle missing run.py' >&2; exit 5; }

echo '[2/8] Installing self-contained files at /root/SiRisky...'
if [[ -d "$TARGET" ]]; then
  rm -rf "${TARGET}.previous"
  mv "$TARGET" "${TARGET}.previous"
fi
mv "$STAGE" "$TARGET"
mkdir -p "$TARGET/CSV" "$TARGET/data" "$TARGET/logs"
chmod 700 "$TARGET" "$TARGET/data" "$TARGET/logs"

# Reuse the existing env *format and named values* without printing secrets.
echo '[3/8] Bootstrapping RPC / Telegram / risk env values...'
python3 - "$TARGET" "$SOURCE_ROOT" <<'PY'
import os, re, sys
from pathlib import Path
root=Path(sys.argv[1]); source=Path(sys.argv[2])

wanted={
 'TELEGRAM_BOT_TOKEN':'TELEGRAM_BOT_TOKEN',
 'TELEGRAM_CHAT_IDS':'TELEGRAM_CHAT_IDS',
 'SOLANA_RPC_URL':'SOLANA_RPC_URL',
 'SOLANA_WS_URL':'SOLANA_WS_URL',
 'JUPITER_API_KEY':'JUPITER_API_KEY',
 'MAX_CAPITAL_USD':'MAX_CAPITAL_USD',
 'MAX_POSITION_USD':'MAX_POSITION_USD',
 'MAX_TOTAL_EXPOSURE_USD':'MAX_TOTAL_EXPOSURE_USD',
 'MAX_OPEN_POSITIONS':'MAX_OPEN_POSITIONS',
 'MAX_DAILY_LOSS_USD':'MAX_DAILY_LOSS_USD',
 'MAX_DRAWDOWN_PCT':'MAX_DRAWDOWN_PCT',
 'CLAUDE_BOT_WALLET_OWNER_ID':'SIRISKY_WALLET_OWNER_ID',
 'SIBOT1_WALLET_OWNER_ID':'SIRISKY_WALLET_OWNER_ID',
}
vals={}
candidates=[source/'.env', source/'.env.local', Path('/var/tmp/ai_council_runtime.env'), Path('/var/tmp/boot/ai_council_runtime.env')]
try:
    candidates += list(source.glob('*.env')) + list(source.glob('config/*.env'))
except Exception:
    pass
for p in candidates:
    if not p.is_file():
        continue
    for raw in p.read_text(encoding='utf-8',errors='replace').splitlines():
        s=raw.strip()
        if not s or s.startswith('#') or '=' not in s: continue
        k,v=s.split('=',1); k=k.strip(); v=v.strip()
        dst=wanted.get(k)
        if dst and v and not vals.get(dst): vals[dst]=v

if not vals.get('SIRISKY_WALLET_OWNER_ID'):
    chats=vals.get('TELEGRAM_CHAT_IDS','')
    m=re.search(r'-?\d{3,24}',chats)
    if m: vals['SIRISKY_WALLET_OWNER_ID']=m.group(0)

# Deterministic standalone paths.
vals['CSV_DIR']=str(root/'CSV')
vals['DATA_DIR']=str(root/'data')
vals['LOG_DIR']=str(root/'logs')
vals['AUTHORISED_CHAINS']='solana'

# Conservative defaults only when the existing runtime has no value.
defaults={
 'MAX_CAPITAL_USD':'100','MAX_POSITION_USD':'5','MAX_TOTAL_EXPOSURE_USD':'5',
 'MAX_OPEN_POSITIONS':'1','MAX_DAILY_LOSS_USD':'5','MAX_DRAWDOWN_PCT':'10'
}
for k,v in defaults.items(): vals.setdefault(k,v)

out=root/'.env'
out.write_text('\n'.join(f'{k}={v}' for k,v in vals.items())+'\n',encoding='utf-8')
os.chmod(out,0o600)
print('env_names_present=' + ','.join(sorted(vals)))
PY

# Best-effort encrypted-wallet bootstrap using existing runtime storage.
echo '[4/8] Looking for the existing encrypted Solana wallet...'
python3 - "$TARGET" "$SOURCE_ROOT" <<'PY'
import csv, os, shutil, sys
from pathlib import Path
root=Path(sys.argv[1]); source=Path(sys.argv[2])
env={}
for raw in (root/'.env').read_text(encoding='utf-8',errors='replace').splitlines():
    if '=' in raw and not raw.lstrip().startswith('#'):
        k,v=raw.split('=',1); env[k.strip()]=v.strip()
owner=env.get('SIRISKY_WALLET_OWNER_ID','')
if not owner:
    print('wallet_bootstrap=SKIP_NO_OWNER'); raise SystemExit(0)

search_roots=[source, Path('/home/ayman01323/ClaudeServer/runtime'), Path('/var/tmp')]
csv_candidates=[]; key_candidates=[]
for base in search_roots:
    if not base.exists(): continue
    try:
        csv_candidates.extend(base.rglob('solana_user_wallets.csv'))
        key_candidates.extend(base.rglob('.solana_wallet_store.key'))
    except Exception: pass

source_csv=None; selected=None
for p in csv_candidates:
    try:
        with p.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
        rows=[r for r in rows if str(r.get('telegram_id','')).strip()==owner and str(r.get('enabled','true')).lower() in {'1','true','yes','on'} and str(r.get('signing','')).lower()=='true']
        if rows:
            selected=next((r for r in rows if str(r.get('active','')).lower()=='true'),rows[0]); source_csv=p; break
    except Exception: pass
if not selected:
    print('wallet_bootstrap=SKIP_NO_SIGNING_METADATA'); raise SystemExit(0)

wallet_id=str(selected.get('wallet_id') or '').strip()
source_key=None; source_enc=None
for kp in key_candidates:
    candidate=kp.parent/'user_solana_wallets'/owner/f'{wallet_id}.enc.json'
    if candidate.is_file(): source_key=kp; source_enc=candidate; break
if not source_key or not source_enc:
    print('wallet_bootstrap=SKIP_ENCRYPTED_KEY_NOT_FOUND'); raise SystemExit(0)

headers=['telegram_id','wallet_id','label','address','signing','enabled','active','created_epoch','notes']
target_csv=root/'CSV'/'wallets.csv'
with target_csv.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=headers); w.writeheader(); w.writerow({h:selected.get(h,'') for h in headers})
shutil.copy2(source_key,root/'data'/'.solana_wallet_store.key')
dest=root/'data'/'user_solana_wallets'/owner
dest.mkdir(parents=True,exist_ok=True)
shutil.copy2(source_enc,dest/source_enc.name)
os.chmod(root/'data'/'.solana_wallet_store.key',0o600)
os.chmod(dest/source_enc.name,0o600)
print('wallet_bootstrap=PASS')
PY

echo '[5/8] Installing Python environment and running unit tests...'
cd "$TARGET"
python3 -m venv .venv
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
.venv/bin/python -m compileall -q .
PYTHONPATH=. .venv/bin/python run.py selftest

# Force operator-controlled SAFE state *after* copying all files.
echo '[6/8] Forcing LIVE/BROADCAST OFF...'
python3 - "$TARGET/CSV/runtime.csv" <<'PY'
import csv,sys
from pathlib import Path
p=Path(sys.argv[1]); rows=list(csv.DictReader(p.open(encoding='utf-8-sig',newline='')))
for r in rows:
    if r.get('setting') in {'live_enabled','broadcast_enabled','telegram_manual_run_enabled'}: r['value']='0'
with p.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['setting','value','notes']); w.writeheader(); w.writerows(rows)
PY

echo '[7/8] Running safe RPC / Jupiter / Telegram / wallet preflight...'
set +e
PYTHONPATH=. .venv/bin/python run.py check
CHECK_RC=$?
set -e

# Install service, but the CSV gates keep all broadcast disabled.
echo '[8/8] Installing SiRisky service in SAFE-OFF mode...'
cp "$TARGET/systemd/sirisky.service" /etc/systemd/system/sirisky.service
systemctl daemon-reload
systemctl enable sirisky.service >/dev/null
systemctl restart sirisky.service
sleep 2
SERVICE_STATE="$(systemctl is-active sirisky.service || true)"

printf '\n=== SIRISKY INSTALL RESULT ===\n'
printf 'target=/root/SiRisky\n'
printf 'selftest=PASS\n'
printf 'preflight_exit_code=%s\n' "$CHECK_RC"
printf 'service_state=%s\n' "$SERVICE_STATE"
printf 'live_enabled=0\n'
printf 'broadcast_enabled=0\n'
printf 'telegram_manual_run_enabled=0\n'
printf 'real_transaction_broadcast=NO\n'
printf 'NEXT=Review and update /root/SiRisky/CSV before LIVE\n'

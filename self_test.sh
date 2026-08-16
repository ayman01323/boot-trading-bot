#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-./.venv/bin/python}
if [ ! -x "$PY" ]; then PY=python3; fi
"$PY" -m compileall -q learnerbot
"$PY" - <<'PY'
import csv
from pathlib import Path
p=Path('CSVbot/chains.csv');rows=list(csv.DictReader(p.open()))
expected={'1','56','137','8453','42161'}
enabled={r['chain_id'] for r in rows if r.get('enabled','').lower()=='true'}
assert enabled==expected,(enabled,expected)
rpc=list(csv.DictReader(Path('CSVbot/rpc_endpoints.csv').open()))
for cid in expected:
 assert any(r.get('chain_id')==cid and r.get('enabled','').lower()=='true' and r.get('url') for r in rpc), f'missing enabled RPC for {cid}'
settings={r['setting']:r['value'] for r in csv.DictReader(Path('CSVbot/auto_trading_settings.csv').open()) if r.get('chain_id')=='*'}
assert settings.get('fast_market_enabled','').lower()=='true'
assert int(settings.get('fast_market_interval_seconds','0'))>=5
seeds=list(csv.DictReader(Path('CSVbot/tokens.csv').open()))
for cid in expected:
 active=[r for r in seeds if r.get('chain_id')==cid and r.get('enabled','').lower()=='true']
 assert any(r.get('role')=='wrapped_base' for r in active), f'missing wrapped base seed {cid}'
 assert any(r.get('role')=='liquid_seed' for r in active), f'missing liquid seed {cid}'
dex=list(csv.DictReader(Path('CSVbot/dex_registry.csv').open()))
for cid in expected:
 assert any(r.get('chain_id')==cid and r.get('version','').upper()=='V2' and r.get('enabled','').lower()=='true' and r.get('router') and r.get('factory') for r in dex), f'missing enabled V2 venue {cid}'
print('Static v2.2 configuration: PASS (5/5 chains, RPCs, V2 venues, liquid seeds, fast scanner)')
PY
if "$PY" -c 'import pytest' >/dev/null 2>&1; then
  "$PY" -m pytest -q
else
  echo 'pytest is not installed in this interpreter; compile/config checks passed.'
fi

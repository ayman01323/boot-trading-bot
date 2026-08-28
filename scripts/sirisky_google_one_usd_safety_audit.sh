#!/usr/bin/env bash
set -Eeuo pipefail
test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323
sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky
echo "=== SIRISKY ONE USD SAFETY AUDIT V2 ==="
echo "--- jupiter.py ---"
sed -n "1,220p" sirisky/jupiter.py 2>/dev/null || true
echo "--- stage5 live ---"
sed -n "1,240p" sirisky/stage5_trade.py 2>/dev/null || true
echo "--- token close tooling ---"
command -v spl-token || true
command -v solana || true
.venv/bin/python - <<"PY"
mods=[]
for m in ("solders","solana","spl","nacl"):
    try:
        __import__(m); mods.append(m+"=yes")
    except Exception: mods.append(m+"=no")
print(" ".join(mods))
PY
echo "audit_v2_read_only=true"
'

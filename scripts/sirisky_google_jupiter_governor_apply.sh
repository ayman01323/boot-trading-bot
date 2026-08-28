#!/usr/bin/env bash
set -Eeuo pipefail

test "$(hostname)" = botgoogle
test "$(id -un)" = ayman01323

sudo -n bash -lc '
set -Eeuo pipefail
cd /root/SiRisky

echo "=== SIRISKY TELEGRAM LIVE INSPECT ==="
echo "--- telegram.py ---"
sed -n "1,280p" sirisky/telegram.py 2>/dev/null || true
echo "--- run.py relevant ---"
grep -n -E "telegram|notice|poll|NewPoll|open_position|send|result" run.py 2>/dev/null | head -n 220 || true
echo "--- engine.py relevant ---"
grep -n -E "OPEN_HEADERS|OPENED|CLOSED|monitor_cycle|entry_cycle|position" sirisky/engine.py 2>/dev/null | head -n 220 || true
echo "--- runtime telegram settings ---"
grep -Ei "telegram|poll|interval|notify" CSV/runtime.csv 2>/dev/null || true
echo "--- candidate headers/sample ---"
head -n 3 CSV/stage1_candidates.csv 2>/dev/null || true
echo "--- selected pools headers/sample ---"
head -n 3 CSV/stage1_selected_pools.csv 2>/dev/null || true
echo "--- open positions ---"
cat CSV/open_positions.csv 2>/dev/null || true
echo "--- closed positions tail ---"
tail -n 8 CSV/closed_positions.csv 2>/dev/null || true
echo "service=$(systemctl is-active sirisky.service)"
echo "inspect_read_only=true"
'

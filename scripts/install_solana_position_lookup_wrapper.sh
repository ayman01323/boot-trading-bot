#!/usr/bin/env bash
set -Eeuo pipefail

BOT_DIR="${BOT_DIR:-/root/multichain-learning-bot-v2.2-fast-direct-market}"
RUNNER_USER="${RUNNER_USER:-github-runner}"
LOOKUP_WRAPPER="/usr/local/sbin/lookup-solana-sibot-position"
SUDOERS="/etc/sudoers.d/github-runner-solana-position-lookup"
DB="$BOT_DIR/data/solana_sibot.sqlite3"
PY="$BOT_DIR/.venv/bin/python"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo -E bash $0" >&2
  exit 2
fi
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  echo "Runner user does not exist: $RUNNER_USER" >&2
  exit 2
fi
if [[ ! -x "$PY" ]]; then
  echo "Expected production Python is missing: $PY" >&2
  exit 2
fi
if [[ ! -f "$DB" ]]; then
  echo "Expected Solana SiBot database is missing: $DB" >&2
  exit 2
fi

TMP_WRAPPER="$(mktemp)"
TMP_SUDOERS="$(mktemp)"
trap 'rm -f "$TMP_WRAPPER" "$TMP_SUDOERS"' EXIT

cat >"$TMP_WRAPPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

if [[ \$# -ne 0 ]]; then
  echo "This command accepts no arguments; provide one position id on stdin." >&2
  exit 2
fi
IFS= read -r POSITION_ID || {
  echo "Missing position id on stdin." >&2
  exit 2
}
if [[ ! "\$POSITION_ID" =~ ^[0-9a-f]{32}\$ ]]; then
  echo "Invalid position id; expected exactly 32 lowercase hexadecimal characters." >&2
  exit 2
fi
if IFS= read -r EXTRA_LINE; then
  if [[ -n "\$EXTRA_LINE" ]]; then
    echo "Unexpected extra input." >&2
    exit 2
  fi
fi

DB="$DB"
PY="$PY"
if [[ ! -f "\$DB" || ! -x "\$PY" ]]; then
  echo "Fixed Solana lookup prerequisites are missing." >&2
  exit 3
fi

exec "\$PY" - "\$DB" "\$POSITION_ID" <<'PYCODE'
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

if len(sys.argv) != 3:
    raise SystemExit("expected fixed database path and one position id")

db = Path(sys.argv[1]).resolve()
position_id = sys.argv[2]
uri = f"file:{quote(db.as_posix(), safe='/')}?mode=ro"
conn = sqlite3.connect(uri, uri=True, timeout=30.0)
conn.row_factory = sqlite3.Row
try:
    conn.execute("PRAGMA query_only=ON")
    row = conn.execute(
        """
        SELECT position_id, mint, mode, status,
               token_amount_raw, entry_cost_sol, entry_ts,
               current_exit_sol, unrealised_net_sol, unrealised_pct,
               realised_net_sol, exit_reason, closed_at, updated_at
          FROM positions
         WHERE position_id = ?
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
finally:
    conn.close()

out = {"schema_version": 1, "found": row is not None, "position_id": position_id}
if row is not None:
    out["position"] = {key: row[key] for key in row.keys()}
print(json.dumps(out, indent=2, sort_keys=True))
PYCODE
EOF

cat >"$TMP_SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $LOOKUP_WRAPPER
EOF

chmod 0755 "$TMP_WRAPPER"
chmod 0440 "$TMP_SUDOERS"
visudo -cf "$TMP_SUDOERS" >/dev/null
install -o root -g root -m 0755 "$TMP_WRAPPER" "$LOOKUP_WRAPPER"
install -o root -g root -m 0440 "$TMP_SUDOERS" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

echo "Installed restricted Solana position lookup wrapper: $LOOKUP_WRAPPER"
echo "Granted $RUNNER_USER exactly one no-argument sudo command via $SUDOERS"
echo "Lookup is fixed to the Solana SiBot positions table and opens SQLite read-only."

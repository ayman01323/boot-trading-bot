#!/usr/bin/env bash
set -Eeuo pipefail

# One-time root bootstrap for the SiBot leader-gate report.
#
# Security model:
# - the GitHub runner remains unprivileged and cannot traverse /root;
# - exactly one no-argument sudo command is granted;
# - the root wrapper accepts no paths, commands, interpreters or environment overrides;
# - the wrapper verifies a clean deployed main checkout before using its tracked code;
# - the report runs from a temporary git-archive snapshot, never from the live tree;
# - live CSV configuration is copied into the temporary snapshot;
# - only consistent read-only backups of the two SiBot SQLite databases are analysed;
# - no wallet, deployment, restart or trading action is exposed to the runner.

BOT_DIR="${BOT_DIR:-/root/multichain-learning-bot-v2.2-fast-direct-market}"
RUNNER_USER="${RUNNER_USER:-github-runner}"
REPO_MATCH="${REPO_MATCH:-ayman01323/boot-trading-bot}"
REPORT_WRAPPER="/usr/local/sbin/run-sibot-leader-gate-report"
SUDOERS="/etc/sudoers.d/github-runner-sibot-leader-gate"
REPORT_SCRIPT="scripts/sibot_leader_gate_report.py"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo -E bash $0" >&2
  exit 2
fi

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  echo "Runner user does not exist: $RUNNER_USER" >&2
  exit 2
fi
if [[ ! -d "$BOT_DIR/.git" ]]; then
  echo "BOT_DIR is not a git checkout: $BOT_DIR" >&2
  exit 2
fi
if [[ ! -x "$BOT_DIR/.venv/bin/python" ]]; then
  echo "Expected production Python is missing: $BOT_DIR/.venv/bin/python" >&2
  exit 2
fi
if [[ ! -f "$BOT_DIR/$REPORT_SCRIPT" ]]; then
  echo "Report script is not deployed yet: $BOT_DIR/$REPORT_SCRIPT" >&2
  exit 2
fi

ORIGIN_URL="$(git -C "$BOT_DIR" remote get-url origin 2>/dev/null || true)"
if [[ "$ORIGIN_URL" != *"$REPO_MATCH"* ]]; then
  echo "Refusing setup: origin does not match $REPO_MATCH" >&2
  echo "origin=$ORIGIN_URL" >&2
  exit 2
fi

TMP_WRAPPER="$(mktemp)"
TMP_SUDOERS="$(mktemp)"
trap 'rm -f "$TMP_WRAPPER" "$TMP_SUDOERS"' EXIT

cat >"$TMP_WRAPPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

if [[ \$# -ne 0 ]]; then
  echo "This command accepts no arguments." >&2
  exit 2
fi

BOT_DIR="$BOT_DIR"
REPO_MATCH="$REPO_MATCH"
REPORT_SCRIPT="$REPORT_SCRIPT"
DATA_DIR="\$BOT_DIR/data"
CSV_DIR="\$BOT_DIR/CSVbot"
PY="\$BOT_DIR/.venv/bin/python"

cd "\$BOT_DIR"
ORIGIN="\$(git remote get-url origin 2>/dev/null || true)"
if [[ "\$ORIGIN" != *"\$REPO_MATCH"* ]]; then
  echo "Refusing report: repository origin mismatch." >&2
  exit 3
fi
if [[ "\$(git branch --show-current)" != "main" ]]; then
  echo "Refusing report: deployed checkout is not on main." >&2
  exit 4
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Refusing report: tracked local changes exist." >&2
  exit 5
fi
if ! git ls-files --error-unmatch "\$REPORT_SCRIPT" >/dev/null 2>&1; then
  echo "Refusing report: report script is not tracked at HEAD." >&2
  exit 6
fi
if [[ ! -x "\$PY" || ! -f "\$REPORT_SCRIPT" ]]; then
  echo "Refusing report: deployed Python/report script is missing." >&2
  exit 7
fi
if [[ ! -d "\$DATA_DIR" || ! -d "\$CSV_DIR" ]]; then
  echo "Refusing report: production data/config directories are missing." >&2
  exit 8
fi
if [[ ! -f "\$DATA_DIR/sibot.sqlite3" || ! -f "\$DATA_DIR/solana_sibot.sqlite3" ]]; then
  echo "Refusing report: required SiBot databases are missing." >&2
  exit 9
fi
command -v tar >/dev/null 2>&1 || {
  echo "Refusing report: tar is required for the isolated code snapshot." >&2
  exit 10
}

DEPLOYED_SHA="\$(git rev-parse HEAD)"
printf 'deployed_sha: %s\n' "\$DEPLOYED_SHA"
printf 'deployed_branch: main\n'

TMP_ROOT="\$(mktemp -d /tmp/sibot-leader-gate.XXXXXX)"
SNAPSHOT="\$TMP_ROOT/repo"
cleanup() {
  rm -rf "\$TMP_ROOT"
}
trap cleanup EXIT
mkdir -p "\$SNAPSHOT" "\$TMP_ROOT/home" "\$TMP_ROOT/tmp"

# Copy exactly the tracked deployed code.  This deliberately excludes .env,
# untracked wallet material and every other untracked production file.
git archive --format=tar HEAD | tar -xf - -C "\$SNAPSHOT"

# Replace repository-default CSV files with a point-in-time copy of the live
# configuration.  Any one-shot migration imported by the patch chain can only
# touch this temporary copy, never production.
rm -rf "\$SNAPSHOT/CSVbot" "\$SNAPSHOT/data"
cp -a "\$CSV_DIR" "\$SNAPSHOT/CSVbot"
mkdir -p "\$SNAPSHOT/data"

# Preserve existing one-shot migration markers so the snapshot composes like
# the already-running production checkout rather than replaying old migrations.
while IFS= read -r -d '' marker; do
  cp -p "\$marker" "\$SNAPSHOT/data/"
done < <(find "\$DATA_DIR" -maxdepth 1 -type f -name '.*' -print0)

# Take consistent SQLite backups while opening the live sources in mode=ro.
# The report then reads only these temporary copies.
"\$PY" - \
  "\$DATA_DIR/sibot.sqlite3" "\$SNAPSHOT/data/sibot.sqlite3" \
  "\$DATA_DIR/solana_sibot.sqlite3" "\$SNAPSHOT/data/solana_sibot.sqlite3" <<'PY'
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

args = sys.argv[1:]
if len(args) != 4:
    raise SystemExit("expected two source/destination database pairs")

for src_text, dst_text in zip(args[0::2], args[1::2]):
    src = Path(src_text).resolve()
    dst = Path(dst_text).resolve()
    if not src.is_file():
        raise FileNotFoundError(src)
    uri = f"file:{quote(src.as_posix(), safe='/')}?mode=ro"
    source = sqlite3.connect(uri, uri=True, timeout=30.0)
    destination = sqlite3.connect(dst)
    try:
        source.execute("PRAGMA query_only=ON")
        source.backup(destination)
    finally:
        destination.close()
        source.close()
PY

REPORT_CMD=(
  env -i
  PATH=/usr/local/bin:/usr/bin:/bin
  HOME="\$TMP_ROOT/home"
  TMPDIR="\$TMP_ROOT/tmp"
  PYTHONNOUSERSITE=1
  PYTHONDONTWRITEBYTECODE=1
  SIBOT_GATE_SNAPSHOT=1
  DATA_DIR="\$SNAPSHOT/data"
  CSV_DIR="\$SNAPSHOT/CSVbot"
  "\$PY"
  "\$SNAPSHOT/\$REPORT_SCRIPT"
)

set +e
if command -v unshare >/dev/null 2>&1 && unshare --net true >/dev/null 2>&1; then
  printf 'network_isolated: true\n'
  unshare --net -- "\${REPORT_CMD[@]}"
  rc=\$?
else
  printf 'network_isolated: false\n'
  "\${REPORT_CMD[@]}"
  rc=\$?
fi
set -e
exit "\$rc"
EOF

cat >"$TMP_SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $REPORT_WRAPPER
EOF

chmod 0755 "$TMP_WRAPPER"
chmod 0440 "$TMP_SUDOERS"
visudo -cf "$TMP_SUDOERS" >/dev/null

install -o root -g root -m 0755 "$TMP_WRAPPER" "$REPORT_WRAPPER"
install -o root -g root -m 0440 "$TMP_SUDOERS" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

echo "Installed restricted SiBot leader-gate wrapper: $REPORT_WRAPPER"
echo "Granted $RUNNER_USER exactly one no-argument sudo command via $SUDOERS"
echo "Production code/data remain inaccessible to the runner; reports execute from a temporary snapshot."

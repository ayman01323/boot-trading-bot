#!/usr/bin/env bash
set -Eeuo pipefail

# One-time root bootstrap for the SiBot leader-gate report.
#
# Security model:
# - the GitHub runner remains unprivileged and cannot traverse /root;
# - exactly one no-argument sudo command is granted;
# - the root wrapper runs only the tracked report script from a clean main checkout;
# - no arbitrary command, path, interpreter, environment override, wallet operation,
#   deployment, restart, or trading action can be supplied by the runner.

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

printf 'deployed_sha: %s\n' "\$(git rev-parse HEAD)"
printf 'deployed_branch: main\n'

exec env -i \
  PATH=/usr/local/bin:/usr/bin:/bin \
  HOME=/root \
  PYTHONDONTWRITEBYTECODE=1 \
  DATA_DIR="\$DATA_DIR" \
  CSV_DIR="\$CSV_DIR" \
  "\$PY" "\$REPORT_SCRIPT"
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
echo "No /root permissions were broadened."

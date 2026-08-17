#!/usr/bin/env bash
set -Eeuo pipefail

# One-time bootstrap for secure GitHub -> VPS deployment.
#
# Security model:
# - GitHub Actions runner runs as an unprivileged github-runner user.
# - The runner cannot sudo arbitrary commands.
# - sudo is restricted to fixed root-owned wrappers for deploy/status/restart/wallet-check.
# - Deployment accepts only an exact 40-char commit SHA and only origin/main.
# - Tracked local changes stop deployment rather than being overwritten.
# - Tests run before learnerbot is restarted.
# - A failed restart rolls code back to the previous commit.
#
# Never commit a GitHub runner registration token. It is short-lived and is
# consumed only during this one-time installation.

REPO_URL="${REPO_URL:-https://github.com/ayman01323/boot-trading-bot}"
REPO_MATCH="${REPO_MATCH:-ayman01323/boot-trading-bot}"
RUNNER_TOKEN="${GITHUB_RUNNER_TOKEN:-}"
RUNNER_NAME="${RUNNER_NAME:-boot-trading-vps}"
RUNNER_LABELS="${RUNNER_LABELS:-boot-vps}"
RUNNER_USER="${RUNNER_USER:-github-runner}"
RUNNER_HOME="${RUNNER_HOME:-/opt/actions-runner}"
BOT_DIR="${BOT_DIR:-/root/multichain-learning-bot-v2.2-fast-direct-market}"
SERVICE_NAME="${SERVICE_NAME:-learnerbot}"

DEPLOY_WRAPPER="/usr/local/sbin/deploy-boot-trading-bot"
STATUS_WRAPPER="/usr/local/sbin/status-boot-trading-bot"
RESTART_WRAPPER="/usr/local/sbin/restart-boot-trading-bot"
WALLET_WRAPPER="/usr/local/sbin/check-boot-wallet"
SUDOERS="/etc/sudoers.d/github-runner-boot-trading"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo -E bash $0" >&2
  exit 2
fi

if [[ -z "$RUNNER_TOKEN" ]]; then
  cat >&2 <<'EOF'
Missing GITHUB_RUNNER_TOKEN.

Create a short-lived repository runner registration token in:
  GitHub -> boot-trading-bot -> Settings -> Actions -> Runners
  -> New self-hosted runner

Then copy ONLY the token value and run:
  export GITHUB_RUNNER_TOKEN='PASTE_SHORT_LIVED_TOKEN'
  sudo -E bash scripts/install_github_deployer.sh

Do not save the token in .env, CSV, shell history files, or GitHub source.
EOF
  exit 2
fi

if [[ ! -d "$BOT_DIR/.git" ]]; then
  echo "BOT_DIR is not a git checkout: $BOT_DIR" >&2
  exit 2
fi
if [[ ! -x "$BOT_DIR/.venv/bin/python" ]]; then
  echo "Expected virtualenv Python is missing: $BOT_DIR/.venv/bin/python" >&2
  exit 2
fi

ORIGIN_URL="$(git -C "$BOT_DIR" remote get-url origin 2>/dev/null || true)"
if [[ "$ORIGIN_URL" != *"$REPO_MATCH"* ]]; then
  echo "Refusing setup: origin does not match $REPO_MATCH" >&2
  echo "origin=$ORIGIN_URL" >&2
  exit 2
fi

if command -v dnf >/dev/null 2>&1; then
  dnf -y install curl tar gzip jq sudo git util-linux >/dev/null
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update -y >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y curl tar gzip jq sudo git util-linux >/dev/null
fi

# Tests are a deployment gate. Install pytest into the existing bot venv once.
"$BOT_DIR/.venv/bin/python" -m pip install -q 'pytest>=8,<9'

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$RUNNER_USER"
fi
mkdir -p "$RUNNER_HOME"
chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME"

# Root-owned deployment wrapper. GitHub may provide only an exact commit SHA.
cat >"$DEPLOY_WRAPPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
TARGET_SHA="\${1:-}"
BOT_DIR="$BOT_DIR"
SERVICE="$SERVICE_NAME"
REPO_MATCH="$REPO_MATCH"
LOG="/var/log/boot-github-deploy.log"

if [[ ! "\$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid deployment SHA" >&2
  exit 2
fi

exec 9>/var/lock/boot-github-deploy.lock
if ! flock -w 60 9; then
  echo "Another deployment is already running" >&2
  exit 3
fi

log() {
  printf '%s %s\n' "\$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "\$*" | tee -a "\$LOG"
}
rollback() {
  local old="\$1"
  log "ROLLBACK -> \$old"
  git reset --hard "\$old"
  systemctl restart "\$SERVICE" || true
}

cd "\$BOT_DIR"
ORIGIN="\$(git remote get-url origin 2>/dev/null || true)"
if [[ "\$ORIGIN" != *"\$REPO_MATCH"* ]]; then
  log "REFUSED origin mismatch: \$ORIGIN"
  exit 4
fi

# Never destroy server-side tracked changes silently.
if ! git diff --quiet || ! git diff --cached --quiet; then
  log "REFUSED tracked local changes exist"
  git status --short --untracked-files=no
  exit 5
fi

git checkout -q main
OLD_SHA="\$(git rev-parse HEAD)"
log "BEGIN current=\$OLD_SHA requested=\$TARGET_SHA"

git fetch --prune origin main
REMOTE_SHA="\$(git rev-parse origin/main)"
if [[ "\$REMOTE_SHA" != "\$TARGET_SHA" ]]; then
  log "REFUSED requested SHA is not current origin/main (origin/main=\$REMOTE_SHA)"
  exit 6
fi

if [[ "\$OLD_SHA" == "\$TARGET_SHA" ]]; then
  log "ALREADY DEPLOYED \$TARGET_SHA"
  systemctl is-active "\$SERVICE"
  exit 0
fi

if ! git merge --ff-only "\$TARGET_SHA"; then
  log "REFUSED non-fast-forward deployment"
  exit 7
fi

PY="\$BOT_DIR/.venv/bin/python"

# Refresh Python dependencies before validation. Private keys/.env/CSVbot/data are
# outside the git update path and are not copied into the Actions workspace.
if ! "\$PY" -m pip install -q -r requirements.txt; then
  log "DEPENDENCY INSTALL FAILED"
  git reset --hard "\$OLD_SHA"
  exit 8
fi

if ! "\$PY" -m compileall -q learnerbot scripts; then
  log "PYTHON COMPILE FAILED"
  git reset --hard "\$OLD_SHA"
  exit 9
fi

if ! "\$PY" -m pytest -q; then
  log "TESTS FAILED; service remains on old running process"
  git reset --hard "\$OLD_SHA"
  exit 10
fi

log "TESTS PASSED; restarting \$SERVICE"
systemctl restart "\$SERVICE"
sleep 4
if ! systemctl is-active --quiet "\$SERVICE"; then
  log "NEW VERSION FAILED TO START"
  rollback "\$OLD_SHA"
  sleep 3
  if ! systemctl is-active --quiet "\$SERVICE"; then
    log "CRITICAL rollback service also failed"
    systemctl status "\$SERVICE" --no-pager -l || true
    exit 12
  fi
  exit 11
fi

NEW_SHA="\$(git rev-parse HEAD)"
VERSION="\$("\$PY" -c 'import learnerbot; print(getattr(learnerbot,"__version__","unknown"))' 2>/dev/null || true)"
log "SUCCESS sha=\$NEW_SHA version=\$VERSION service=active"
systemctl status "\$SERVICE" --no-pager -l | tail -n 18 || true
EOF
chmod 0755 "$DEPLOY_WRAPPER"
chown root:root "$DEPLOY_WRAPPER"

cat >"$STATUS_WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$BOT_DIR"
echo "=== BOOT SERVER STATUS ==="
echo "UTC: \$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
echo "branch: \$(git branch --show-current)"
echo "sha:    \$(git rev-parse HEAD)"
echo "origin: \$(git remote get-url origin)"
echo "version: \$(.venv/bin/python -c 'import learnerbot; print(getattr(learnerbot,"__version__","unknown"))' 2>/dev/null || true)"
echo
echo "tracked changes:"
git status --short --untracked-files=no || true
echo
echo "service:"
systemctl status "$SERVICE_NAME" --no-pager -l | tail -n 25 || true
EOF
chmod 0755 "$STATUS_WRAPPER"
chown root:root "$STATUS_WRAPPER"

cat >"$RESTART_WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
systemctl restart "$SERVICE_NAME"
sleep 3
systemctl is-active "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager -l | tail -n 20 || true
EOF
chmod 0755 "$RESTART_WRAPPER"
chown root:root "$RESTART_WRAPPER"

cat >"$WALLET_WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
WALLET="\${1:-}"
if [[ ! "\$WALLET" =~ ^0x[0-9A-Fa-f]{40}$ ]]; then
  echo "Invalid wallet address" >&2
  exit 2
fi
cd "$BOT_DIR"
exec .venv/bin/python scripts/check_wallets.py --wallet "\$WALLET"
EOF
chmod 0755 "$WALLET_WRAPPER"
chown root:root "$WALLET_WRAPPER"

cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $DEPLOY_WRAPPER *
$RUNNER_USER ALL=(root) NOPASSWD: $STATUS_WRAPPER
$RUNNER_USER ALL=(root) NOPASSWD: $RESTART_WRAPPER
$RUNNER_USER ALL=(root) NOPASSWD: $WALLET_WRAPPER *
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

# Resolve the current official Actions runner release at install time.
RUNNER_VERSION="$(curl -fsSL -H 'Accept: application/vnd.github+json' https://api.github.com/repos/actions/runner/releases/latest | jq -r '.tag_name' | sed 's/^v//')"
if [[ -z "$RUNNER_VERSION" || "$RUNNER_VERSION" == "null" ]]; then
  echo "Could not resolve GitHub Actions runner version" >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64|amd64) ARCH="x64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 2 ;;
esac
PKG="actions-runner-linux-${ARCH}-${RUNNER_VERSION}.tar.gz"
URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${PKG}"

echo "Installing GitHub Actions runner v${RUNNER_VERSION} into ${RUNNER_HOME}"
cd "$RUNNER_HOME"
if [[ ! -x ./config.sh ]]; then
  curl -fL "$URL" -o "$PKG"
  tar xzf "$PKG"
  rm -f "$PKG"
  chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME"
fi

if [[ ! -f .runner ]]; then
  sudo -u "$RUNNER_USER" ./config.sh \
    --unattended \
    --url "$REPO_URL" \
    --token "$RUNNER_TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$RUNNER_LABELS" \
    --work _work \
    --replace
else
  echo "Runner is already configured; keeping existing registration."
fi

./svc.sh install "$RUNNER_USER" 2>/dev/null || true
./svc.sh start
./svc.sh status || true

cat <<EOF

============================================================
GitHub -> VPS deployment bridge is installed.
============================================================
Repository : $REPO_URL
Runner     : $RUNNER_NAME
Labels     : $RUNNER_LABELS
Bot path   : $BOT_DIR
Service    : $SERVICE_NAME

From now on:
1. a change is merged/pushed to GitHub main;
2. GitHub Actions sends the exact main commit SHA to this runner;
3. the root-owned deploy wrapper verifies origin/main;
4. dependencies + compile + pytest run;
5. learnerbot restarts only after tests pass;
6. failed startup rolls back to the prior code revision.

Available safe server operations through GitHub Actions:
- deploy current main
- show server/service status
- restart learnerbot
- check a public wallet balance

GitHub Actions does NOT receive your .env, private keys, encrypted wallet files,
or unrestricted root shell access through this setup.
EOF

#!/usr/bin/env bash
set -euo pipefail

# One-time bootstrap for a dedicated GitHub Actions self-hosted runner.
# The runner user gets sudo permission for ONE fixed read-only wallet-check wrapper only.
# Never put the runner registration token in this file or commit it to GitHub.

REPO_URL="${REPO_URL:-https://github.com/ayman01323/boot-trading-bot}"
RUNNER_TOKEN="${GITHUB_RUNNER_TOKEN:-}"
RUNNER_NAME="${RUNNER_NAME:-boot-wallet-vps}"
RUNNER_LABELS="${RUNNER_LABELS:-boot-wallet,linux,x64}"
RUNNER_USER="${RUNNER_USER:-github-runner}"
RUNNER_HOME="${RUNNER_HOME:-/opt/actions-runner}"
BOT_DIR="${BOT_DIR:-/root/multichain-learning-bot-v2.2-fast-direct-market}"
WRAPPER="/usr/local/sbin/boot-wallet-check"
SUDOERS="/etc/sudoers.d/github-runner-boot-wallet-check"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo -E bash $0" >&2
  exit 2
fi
if [[ -z "$RUNNER_TOKEN" ]]; then
  cat >&2 <<'EOF'
Missing GITHUB_RUNNER_TOKEN.
Create a short-lived self-hosted runner registration token in:
GitHub repo -> Settings -> Actions -> Runners -> New self-hosted runner
Then run, for example:
  export GITHUB_RUNNER_TOKEN='PASTE_SHORT_LIVED_TOKEN'
  sudo -E bash scripts/install_github_runner.sh
Do NOT save the token in a file or commit it.
EOF
  exit 2
fi
if [[ ! -d "$BOT_DIR" ]]; then
  echo "BOT_DIR does not exist: $BOT_DIR" >&2
  exit 2
fi
if [[ ! -f "$BOT_DIR/scripts/check_wallets.py" ]]; then
  echo "Missing $BOT_DIR/scripts/check_wallets.py" >&2
  echo "Pull the repository update containing scripts/check_wallets.py first." >&2
  exit 2
fi

if command -v dnf >/dev/null 2>&1; then
  dnf -y install curl tar gzip jq sudo >/dev/null
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update -y >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y curl tar gzip jq sudo >/dev/null
fi

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$RUNNER_USER"
fi
mkdir -p "$RUNNER_HOME"
chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_HOME"

# Build a root-owned wrapper with a FIXED bot path. The workflow can supply only the wallet address.
cat >"$WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
WALLET="\${1:-}"
if [[ ! "\$WALLET" =~ ^0x[0-9A-Fa-f]{40}$ ]]; then
  echo "Invalid wallet address" >&2
  exit 2
fi
cd "$BOT_DIR"
PY="python3"
if [[ -x .venv/bin/python ]]; then PY=".venv/bin/python"; fi
exec "\$PY" scripts/check_wallets.py --wallet "\$WALLET" --json-out /tmp/boot_wallet_balances.json
EOF
chmod 0755 "$WRAPPER"
chown root:root "$WRAPPER"

cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $WRAPPER *
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

# Resolve the current GitHub Actions runner release at install time.
RUNNER_VERSION="$(curl -fsSL -H 'Accept: application/vnd.github+json' https://api.github.com/repos/actions/runner/releases/latest | jq -r '.tag_name' | sed 's/^v//')"
if [[ -z "$RUNNER_VERSION" || "$RUNNER_VERSION" == "null" ]]; then
  echo "Could not resolve GitHub Actions runner version" >&2
  exit 1
fi
ARCH="x64"
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

# Configure once. Registration token is consumed here and is not retained by this script.
if [[ ! -f .runner ]]; then
  sudo -u "$RUNNER_USER" ./config.sh \
    --unattended \
    --url "$REPO_URL" \
    --token "$RUNNER_TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$RUNNER_LABELS" \
    --work _work \
    --replace
fi

# Install as a system service under the dedicated user.
./svc.sh install "$RUNNER_USER" || true
./svc.sh start
./svc.sh status || true

cat <<EOF

Runner installed.
Repository: $REPO_URL
Runner:     $RUNNER_NAME
Labels:     $RUNNER_LABELS
Bot path:   $BOT_DIR

Security model:
- runner service does NOT run as root;
- runner cannot execute arbitrary sudo commands;
- sudo is limited to $WRAPPER;
- wrapper accepts only a validated 0x wallet address;
- wallet checker performs read-only RPC calls and does not read private keys.

Next: merge/enable the workflow .github/workflows/server-wallet-check.yml,
then use GitHub Actions -> Server wallet balance check -> Run workflow.
EOF

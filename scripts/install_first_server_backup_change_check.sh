#!/usr/bin/env bash
set -Eeuo pipefail

# One-time root installer for a narrowly-scoped backup change checker.
# It does NOT grant the GitHub runner arbitrary sudo access and it never prints
# file contents. The wrapper emits only a SHA-256 metadata fingerprint and a
# changed=true/false decision.

RUNNER_USER="${RUNNER_USER:-github-runner}"
SOURCE="${SOURCE:-/root/multichain-learning-bot-v2.2-fast-direct-market}"
WRAPPER="/usr/local/sbin/check-first-server-backup-change"
STATE_DIR="/var/lib/boot-first-server-backup"
STATE_FILE="$STATE_DIR/last-success.sha256"
SUDOERS="/etc/sudoers.d/github-runner-first-server-backup-check"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 2
fi

id "$RUNNER_USER" >/dev/null 2>&1 || { echo "Runner user not found: $RUNNER_USER" >&2; exit 3; }
test -d "$SOURCE" || { echo "Backup source not found: $SOURCE" >&2; exit 4; }

install -d -m 0700 -o root -g root "$STATE_DIR"

cat >"$WRAPPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
SOURCE='$SOURCE'
STATE_FILE='$STATE_FILE'
ACTION="\${1:-check}"

fingerprint() {
  # Metadata-only inventory: file type, relative path, byte size, mtime and
  # symlink target. Sorting makes the result deterministic and detects adds,
  # deletes and renames without reading secret file contents.
  find "\$SOURCE" -xdev -printf '%y|%P|%s|%T@|%l\\n' \
    | LC_ALL=C sort \
    | sha256sum \
    | awk '{print \$1}'
}

case "\$ACTION" in
  current)
    fingerprint
    ;;
  check)
    CURRENT="\$(fingerprint)"
    PREVIOUS="\$(cat "\$STATE_FILE" 2>/dev/null || true)"
    printf 'fingerprint=%s\\n' "\$CURRENT"
    if [[ -n "\$PREVIOUS" && "\$CURRENT" == "\$PREVIOUS" ]]; then
      echo 'changed=false'
    else
      echo 'changed=true'
    fi
    ;;
  mark)
    VALUE="\${2:-}"
    [[ "\$VALUE" =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid SHA-256 fingerprint' >&2; exit 5; }
    umask 077
    printf '%s\\n' "\$VALUE" >"\$STATE_FILE"
    chmod 0600 "\$STATE_FILE"
    echo 'marked=true'
    ;;
  *)
    echo 'Usage: check-first-server-backup-change {check|current|mark SHA256}' >&2
    exit 2
    ;;
esac
EOF

chmod 0755 "$WRAPPER"
chown root:root "$WRAPPER"

cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $WRAPPER check
$RUNNER_USER ALL=(root) NOPASSWD: $WRAPPER current
$RUNNER_USER ALL=(root) NOPASSWD: $WRAPPER mark *
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

echo "Installed: $WRAPPER"
echo "State:     $STATE_FILE"
echo "Runner:    $RUNNER_USER"
echo "Source:    $SOURCE"
echo "Test: sudo -u $RUNNER_USER sudo -n $WRAPPER check"

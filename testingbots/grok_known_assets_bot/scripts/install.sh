#!/usr/bin/env bash
set -euo pipefail

TARGET="/home/ayman01323/BOOT/testingbots/grok_known_assets_bot"
SOURCE="${1:-$(pwd)}"

mkdir -p "$TARGET"
# Runtime state must survive code rsync.  state.sqlite3 contains the PAPER audit
# journal/position snapshots and grok_control.json contains the user's PAPER arm.
rsync -a --delete \
  --exclude '.venv/' \
  --exclude '.env' \
  --exclude 'state.sqlite3' \
  --exclude 'grok_control.json' \
  "$SOURCE/" "$TARGET/"
cd "$TARGET"
cp -n config.example.json config.json
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -q -e '.[test]'
.venv/bin/pytest -q
.venv/bin/grok-known-assets-bot --config config.json --db state.sqlite3 check

echo "deployed=$TARGET"
echo "mode=PAPER_ONLY"

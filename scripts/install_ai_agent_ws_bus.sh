#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEST_DIR="${AI_AGENT_BUS_INSTALL_DIR:-/opt/boot-ai-agent-bus}"
VENV="$DEST_DIR/.venv"
DATA_DIR="${AI_AGENT_BUS_DATA_DIR:-/var/tmp/boot}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "install_ai_agent_ws_bus.sh must run as root" >&2
  exit 1
fi

install -d -m 0755 "$DEST_DIR/scripts" "$DEST_DIR/learnerbot" "$DATA_DIR"
install -m 0644 "$SOURCE_DIR/scripts/ai_agent_ws_bus.py" "$DEST_DIR/scripts/ai_agent_ws_bus.py"
install -m 0644 "$SOURCE_DIR/scripts/ai_agent_ws_bus_grok.py" "$DEST_DIR/scripts/ai_agent_ws_bus_grok.py"
install -m 0644 "$SOURCE_DIR/scripts/ai_agent_ws_worker.py" "$DEST_DIR/scripts/ai_agent_ws_worker.py"
install -m 0644 "$SOURCE_DIR/scripts/ai_agent_ws_memory.py" "$DEST_DIR/scripts/ai_agent_ws_memory.py"
install -m 0644 "$SOURCE_DIR/scripts/strategy_factory_transport.py" "$DEST_DIR/scripts/strategy_factory_transport.py"
install -m 0644 "$SOURCE_DIR/scripts/strategy_factory_chat.py" "$DEST_DIR/scripts/strategy_factory_chat.py"
install -m 0644 "$SOURCE_DIR/scripts/ai_agent_ws_send.py" "$DEST_DIR/scripts/ai_agent_ws_send.py"
install -m 0644 "$SOURCE_DIR/scripts/ai_agent_task_executor.py" "$DEST_DIR/scripts/ai_agent_task_executor.py"

# Strategy Factory is intentionally a minimal communication-only runtime.  Do
# not copy learnerbot/__init__.py here: production learnerbot package startup
# hooks import trading/runtime modules which are neither required nor installed
# on a standalone Strategy Factory host.  A side-effect-free package marker
# keeps provider adapters importable without coupling messaging availability to
# the trading application composition.
cat >"$DEST_DIR/learnerbot/__init__.py" <<'PY'
from __future__ import annotations

__version__ = "strategy-factory-runtime"
PY
chmod 0644 "$DEST_DIR/learnerbot/__init__.py"

install -m 0644 "$SOURCE_DIR/learnerbot/ai_council.py" "$DEST_DIR/learnerbot/ai_council.py"
install -m 0644 "$SOURCE_DIR/learnerbot/ai_council_http_patch.py" "$DEST_DIR/learnerbot/ai_council_http_patch.py"
install -m 0644 "$SOURCE_DIR/learnerbot/provider_current_api_patch.py" "$DEST_DIR/learnerbot/provider_current_api_patch.py"
install -m 0644 "$SOURCE_DIR/learnerbot/ai_runtime_secret_fallback_patch.py" "$DEST_DIR/learnerbot/ai_runtime_secret_fallback_patch.py"
install -m 0644 "$SOURCE_DIR/learnerbot/grok_provider.py" "$DEST_DIR/learnerbot/grok_provider.py"
install -m 0644 "$SOURCE_DIR/learnerbot/kimi_provider.py" "$DEST_DIR/learnerbot/kimi_provider.py"
install -m 0644 "$SOURCE_DIR/learnerbot/ai_cost_router.py" "$DEST_DIR/learnerbot/ai_cost_router.py"
install -m 0644 "$SOURCE_DIR/learnerbot/ai_cost_grok_patch.py" "$DEST_DIR/learnerbot/ai_cost_grok_patch.py"
install -m 0644 "$SOURCE_DIR/learnerbot/ai_cost_kimi_patch.py" "$DEST_DIR/learnerbot/ai_cost_kimi_patch.py"
install -m 0644 "$SOURCE_DIR/learnerbot/ai_cost_provider_patch.py" "$DEST_DIR/learnerbot/ai_cost_provider_patch.py"

if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -q \
  'websockets>=15,<16' 'python-dotenv>=1,<2'

# Fail the deployment before touching service state if the deliberately minimal
# provider dependency closure is incomplete.  This would have caught the Google
# worker crash that previously occurred only after systemd started the service.
PYTHONPATH="$DEST_DIR" "$VENV/bin/python" - <<'PY'
from learnerbot.ai_cost_provider_patch import call_provider
from scripts.ai_agent_ws_worker import low_cost_model
assert callable(call_provider)
assert low_cost_model("deepseek")
print("strategy-factory-provider-import=ok")
PY

cat >/etc/systemd/system/boot-ai-agent-bus.service <<EOF
[Unit]
Description=Boot local WebSocket AI agent bus
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DEST_DIR
Environment=PYTHONPATH=$DEST_DIR
Environment=PYTHONUNBUFFERED=1
Environment=AI_AGENT_BUS_HOST=127.0.0.1
Environment=AI_AGENT_BUS_PORT=8765
Environment=AI_AGENT_BUS_DB=$DATA_DIR/ai_agent_bus.sqlite3
ExecStart=$VENV/bin/python $DEST_DIR/scripts/ai_agent_ws_bus_grok.py
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/boot-ai-agent-worker@.service <<EOF
[Unit]
Description=Boot AI agent WebSocket worker (%i)
After=network-online.target boot-ai-agent-bus.service
Requires=boot-ai-agent-bus.service

[Service]
Type=simple
WorkingDirectory=$DEST_DIR
Environment=PYTHONPATH=$DEST_DIR
Environment=PYTHONUNBUFFERED=1
Environment=AI_AGENT_BUS_URL=ws://127.0.0.1:8765
Environment=AI_AGENT_BUS_DB=$DATA_DIR/ai_agent_bus.sqlite3
Environment=AI_COUNCIL_RUNTIME_ENV=$DATA_DIR/ai_council_runtime.env
Environment=PATH=/root/.local/bin:/root/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=$VENV/bin/python $DEST_DIR/scripts/ai_agent_ws_worker.py --agent %i
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now boot-ai-agent-bus.service
for agent in gpt claude gemini deepseek grok kimi copilot; do
  systemctl enable --now "boot-ai-agent-worker@${agent}.service"
done

sleep 1
systemctl is-active --quiet boot-ai-agent-bus.service
for agent in gpt claude gemini deepseek grok kimi copilot; do
  systemctl is-active --quiet "boot-ai-agent-worker@${agent}.service"
done

echo "AI agent WebSocket bus installed and active on 127.0.0.1:8765 with seven workers"

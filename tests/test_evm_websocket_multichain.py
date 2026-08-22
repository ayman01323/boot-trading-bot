from __future__ import annotations

import os
import subprocess
import sys


def _run(script: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for name in (
        "ALCHEMY_API_KEY",
        "POLYGON_WS_URL", "POLYGON_ALCHEMY_API_KEY",
        "ARBITRUM_WS_URL", "ARBITRUM_ALCHEMY_API_KEY",
        "BNB_WS_URL", "BNB_ALCHEMY_API_KEY", "BSC_WS_URL", "BSC_ALCHEMY_API_KEY",
        "BASE_WS_URL", "BASE_ALCHEMY_API_KEY",
    ):
        env.pop(name, None)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_supported_evm_websocket_chains_are_csv_only():
    script = r'''
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from learnerbot import polygon_websocket_patch as mod

with TemporaryDirectory() as td:
    csv_dir = Path(td)
    (csv_dir / "rpc_endpoints.csv").write_text(
        "chain_id,name,url,ws_url,enabled,priority\n"
        "137,alchemy,,wss://polygon.example/v2/polygon-secret,true,1\n"
        "42161,alchemy,,wss://arbitrum.example/v2/arbitrum-secret,true,1\n"
        "56,alchemy,,wss://bnb.example/v2/bnb-secret,true,1\n"
        "8453,alchemy,,wss://base.example/v2/base-secret,true,1\n",
        encoding="utf-8",
    )
    app = SimpleNamespace(csv_dir=csv_dir)
    assert mod._chain_ws_url(137, app) == "wss://polygon.example/v2/polygon-secret"
    assert mod._chain_ws_url(42161, app) == "wss://arbitrum.example/v2/arbitrum-secret"
    assert mod._chain_ws_url(56, app) == "wss://bnb.example/v2/bnb-secret"
    assert mod._chain_ws_url(8453, app) == "wss://base.example/v2/base-secret"
print("EVM_WS_CSV_ONLY_OK")
'''
    result = _run(script)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "EVM_WS_CSV_ONLY_OK" in result.stdout


def test_priority_and_enabled_rows_are_respected():
    script = r'''
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from learnerbot import polygon_websocket_patch as mod

with TemporaryDirectory() as td:
    csv_dir = Path(td)
    (csv_dir / "rpc_endpoints.csv").write_text(
        "chain_id,name,url,ws_url,enabled,priority\n"
        "137,disabled,,wss://disabled.example/v2/key,false,0\n"
        "137,secondary,,wss://secondary.example/v2/key,true,2\n"
        "137,primary,,wss://primary.example/v2/key,true,1\n",
        encoding="utf-8",
    )
    app = SimpleNamespace(csv_dir=csv_dir)
    assert mod._chain_ws_url(137, app) == "wss://primary.example/v2/key"
print("EVM_WS_PRIORITY_OK")
'''
    result = _run(script)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "EVM_WS_PRIORITY_OK" in result.stdout


def test_env_is_ignored_and_placeholders_are_rejected():
    script = r'''
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from learnerbot import polygon_websocket_patch as mod

with TemporaryDirectory() as td:
    csv_dir = Path(td)
    (csv_dir / "rpc_endpoints.csv").write_text(
        "chain_id,name,url,ws_url,enabled,priority\n"
        "8453,alchemy,,wss://base.example/v2/${ALCHEMY_API_KEY},true,1\n",
        encoding="utf-8",
    )
    app = SimpleNamespace(csv_dir=csv_dir)
    assert mod._chain_ws_url(8453, app) == ""
    assert mod._chain_ws_url(42161, app) == ""
print("EVM_WS_ENV_IGNORED_OK")
'''
    result = _run(
        script,
        {
            "ALCHEMY_API_KEY": "must-not-be-used",
            "BASE_WS_URL": "wss://env.example/base-secret",
            "ARBITRUM_ALCHEMY_API_KEY": "must-not-be-used",
        },
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "EVM_WS_ENV_IGNORED_OK" in result.stdout


def test_missing_csv_returns_no_websocket_url():
    script = r'''
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from learnerbot import polygon_websocket_patch as mod

with TemporaryDirectory() as td:
    app = SimpleNamespace(csv_dir=Path(td))
    assert mod._chain_ws_url(137, app) == ""
    assert mod._chain_ws_url(999999, app) == ""
print("EVM_WS_MISSING_CSV_OK")
'''
    result = _run(script, {"ALCHEMY_API_KEY": "still-must-not-be-used"})
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "EVM_WS_MISSING_CSV_OK" in result.stdout

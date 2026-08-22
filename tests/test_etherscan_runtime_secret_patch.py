from __future__ import annotations

import importlib
import os
import stat
from pathlib import Path


def test_runtime_key_requires_private_file(tmp_path, monkeypatch):
    path = tmp_path / "etherscan.env"
    path.write_text('ETHERSCAN_API_KEY="test-key"\n', encoding="utf-8")
    path.chmod(0o644)
    monkeypatch.setenv("ETHERSCAN_RUNTIME_ENV", str(path))

    import learnerbot.etherscan_runtime_secret_patch as patch
    importlib.reload(patch)
    assert patch._runtime_key() == ""

    path.chmod(0o600)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert patch._runtime_key() == "test-key"


def test_runtime_bridge_only_fills_missing_key(tmp_path, monkeypatch):
    path = tmp_path / "etherscan.env"
    path.write_text('ETHERSCAN_API_KEY="bridge-key"\n', encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("ETHERSCAN_RUNTIME_ENV", str(path))
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)

    import learnerbot.config as config
    import learnerbot.etherscan_runtime_secret_patch as patch
    importlib.reload(patch)
    app = config.AppSettings.load()
    assert app.etherscan_api_key == "bridge-key"

    monkeypatch.setenv("ETHERSCAN_API_KEY", "process-key")
    app = config.AppSettings.load()
    assert app.etherscan_api_key == "process-key"

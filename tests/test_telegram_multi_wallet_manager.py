from pathlib import Path
from types import SimpleNamespace

from learnerbot.multi_wallet_store import MultiWalletStore
from learnerbot.solana_wallet_store import SolanaWalletStore
from learnerbot import telegram_multi_wallet_manager_patch as patch


SOL1 = "5sdV3Rr2CV5uLZozZWwtpTSad31Hvi9FniYUAHCfEqbw"
SOL2 = "So11111111111111111111111111111111111111112"


def _app(tmp_path: Path):
    return SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "csv")


def test_hub_shows_multiple_evm_and_solana_wallets(tmp_path, monkeypatch):
    app = _app(tmp_path)
    monkeypatch.setattr(patch._ui, "require_user", lambda *args, **kwargs: {"status": "ACTIVE"})

    evm = MultiWalletStore(app.data_dir, app.csv_dir)
    evm._max_wallets = lambda telegram_id: 5
    first_evm = evm.create("123", "EVM-1")
    second_evm = evm.create("123", "EVM-2")
    assert first_evm["active"] is True
    assert second_evm["active"] is False

    sol = SolanaWalletStore(app.csv_dir)
    sol._max_wallets = lambda telegram_id: 5
    first_sol = sol.add("123", SOL1, "SOL-1")
    second_sol = sol.add("123", SOL2, "SOL-2")
    assert first_sol["active"] == "true"
    assert second_sol["active"] == "false"

    text = patch.wallet_hub_page(app, "123")
    assert "EVM wallets:</b> 2" in text
    assert "Solana wallets:</b> 2" in text
    assert "EVM-1" in text
    assert "SOL-1" in text

    kb = patch.wallet_hub_keyboard()
    callbacks = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
    assert "evmwallet:open" in callbacks
    assert "solwallet:open" in callbacks


def test_evm_manager_supports_create_select_and_remove_buttons(tmp_path, monkeypatch):
    app = _app(tmp_path)
    monkeypatch.setattr(patch._ui, "require_user", lambda *args, **kwargs: {"status": "ACTIVE"})

    evm = MultiWalletStore(app.data_dir, app.csv_dir)
    evm._max_wallets = lambda telegram_id: 5
    evm.create("123", "Primary")
    second = evm.create("123", "Backup")

    kb = patch.evmwallet_keyboard(app, "123")
    callbacks = [b.get("callback_data") for row in kb["inline_keyboard"] for b in row]
    assert "evmwallet:create" in callbacks
    assert "evmwallet:import" in callbacks
    assert f"evmwallet:use:{second['wallet_id']}" in callbacks
    assert f"evmwallet:remove:{second['wallet_id']}" in callbacks

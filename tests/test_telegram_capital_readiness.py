from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from learnerbot import telegram_capital_readiness_patch as p


def test_capital_page_keeps_polygon_and_zero_balance_chains_visible(monkeypatch, tmp_path):
    app = SimpleNamespace(csv_dir=Path(tmp_path), operator_settings=lambda: {"engine_enabled": "true"})
    chains = [
        SimpleNamespace(chain_id=56, slug="bsc", name="BSC", native_symbol="BNB"),
        SimpleNamespace(chain_id=137, slug="polygon", name="Polygon", native_symbol="POL"),
    ]
    data = {
        "user": {"status": "ACTIVE", "fee_plan_id": "MASTER", "telegram_id": "1", "can_auto_trade": "true"},
        "wallets": [{
            "active": "true", "label": "Imported", "wallet_id": "evm-1",
            "address": "0x1111111111111111111111111111111111111111",
            "capital_usd": Decimal("2.50"),
            "chains": [
                {
                    "chain_id": 56, "chain_slug": "bsc", "trading_state": "OFF",
                    "native_balance": Decimal("0"), "rpc_ok": True, "error": "",
                    "assets": [], "capital_usd": Decimal("0"), "unpriced_assets": 0,
                },
                {
                    "chain_id": 137, "chain_slug": "polygon", "trading_state": "AUTO",
                    "native_balance": Decimal("5"), "rpc_ok": True, "error": "",
                    "assets": [{"symbol": "POL", "balance": Decimal("5"), "usd_price": Decimal("0.5"), "usd_value": Decimal("2.5")}],
                    "capital_usd": Decimal("2.5"), "unpriced_assets": 0,
                },
            ],
        }],
        "capital_usd": Decimal("2.50"),
        "performance": {"trades": 0, "net_usd": Decimal("0"), "fees_usd": Decimal("0")},
    }
    monkeypatch.setattr(p._cap, "user_dashboard_data", lambda app, tid: data)
    monkeypatch.setattr(p._cap, "load_chains", lambda app, enabled_only=True: chains)
    monkeypatch.setattr(
        p._cap, "load_kv_scoped",
        lambda path, cid: {"trading_enabled": "true", "auto_trading_enabled": "true", "min_native_gas_reserve": "1"},
    )
    monkeypatch.setattr(
        p._cap, "user_setting",
        lambda csv, tid, cid, key, default=None: "1" if key == "min_native_gas_reserve" else "ARMED" if key == "recommendation_mode" else default,
    )
    monkeypatch.setattr(p._cap, "user_bool", lambda *args, **kwargs: True)
    monkeypatch.setattr(p._polygon, "focus_enabled", lambda app: True)
    monkeypatch.setattr(p._solcompat, "_sol_user_section", lambda app, tid: ["<b>🟣 SOLANA CAPITAL &amp; P&amp;L</b>"])

    text = p.user_dashboard_text(app, "1")
    assert "BSC" in text
    assert "Assets: <b>0</b>" in text
    assert "POLYGON" in text
    assert "POL <b>5</b> ≈ <b>$2.50</b>" in text
    assert "gas reserve <b>1 POL</b>" in text
    assert "gas <b>✅ READY</b>" in text
    assert "Polygon-only focus <b>ON</b>" in text
    assert "SOLANA CAPITAL" in text


def test_wallet_hub_removes_stale_solana_shadow_only_wording(monkeypatch):
    stale = (
        "Wallets\n⚠️ An imported Solana signing key is stored for future LIVE capability, but Solana SiBot remains "
        "SHADOW-only until its transaction signing/broadcast engine is separately enabled."
    )
    monkeypatch.setattr(p, "_PREV_WALLET_HUB", lambda app, tid: stale)
    text = p.wallet_hub_page(SimpleNamespace(), "1")
    assert "SHADOW-only" not in text
    assert "Solana LIVE capability is installed" in text
    assert "separate Solana LIVE switch" in text

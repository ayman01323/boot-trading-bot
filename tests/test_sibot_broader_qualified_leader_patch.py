from decimal import Decimal
from types import SimpleNamespace

from learnerbot import sibot_broader_qualified_leader_patch as patch


def test_candidate_beyond_public_top20_can_fill_leader_slot_without_lowering_gate(monkeypatch, tmp_path):
    app = SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")
    chain = SimpleNamespace(chain_id=56, slug="bsc")
    wallets = ["0x" + f"{i:040x}" for i in range(1, 22)]

    # Establish the real SiBot schema used by the patch.
    patch._sibot.connect(app).close()

    monkeypatch.setattr(patch, "_PREV_REFRESH", lambda *args, **kwargs: ["public-top20-preserved"])
    monkeypatch.setattr(patch, "_broad_candidates", lambda *args, **kwargs: wallets)
    monkeypatch.setattr(
        patch._sibot,
        "user_settings",
        lambda *args, **kwargs: {"recent_trade_window": "20", "leaders_per_chain": "3"},
    )
    monkeypatch.setattr(patch._sibot, "export_rankings", lambda *args, **kwargs: None)
    monkeypatch.setattr(patch, "_write_bridge", lambda *args, **kwargs: None)

    def metrics(_app, _chain_id, wallet, _lookback, _recent):
        return {
            "net": Decimal("1"),
            "win_rate": Decimal("70"),
            "profit_factor": Decimal("2"),
            "recent_profit_factor": Decimal("2"),
            "closed": 60,
            "wallet": wallet,
        }

    monkeypatch.setattr(patch._guard, "quality_metrics", metrics)
    # Simulate the exact ordering defect: the first 20 display candidates fail a
    # strict quality gate, while candidate 21 passes it. We are changing search
    # scope only; the gate itself remains authoritative.
    monkeypatch.setattr(
        patch._guard,
        "_leader_quality_ok",
        lambda m, cfg: (m["wallet"] == wallets[-1], "PASS" if m["wallet"] == wallets[-1] else "historical profit factor below minimum"),
    )
    monkeypatch.setattr(patch._guard, "_quality_score", lambda *args, **kwargs: Decimal("1"))

    result = patch.refresh_rankings(app, "123", chain)
    assert result == ["public-top20-preserved"]

    with patch._sibot.connect(app) as conn:
        rows = conn.execute(
            "SELECT wallet,rank FROM leaders WHERE telegram_id=? AND chain_id=? ORDER BY rank",
            ("123", 56),
        ).fetchall()
    assert [(row["wallet"], row["rank"]) for row in rows] == [(wallets[-1], 1)]

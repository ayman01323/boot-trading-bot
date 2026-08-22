from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

from learnerbot import sibot_quality_compat_patch as compat
from learnerbot import sibot_profit_guard_runtime_compat_patch as runtime_compat


HEADERS = ["chain_id", "setting", "value", "description"]


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return {str(r["setting"]): str(r["value"]) for r in csv.DictReader(fh)}


def test_quality_compat_relaxes_only_history_complete(tmp_path):
    path = tmp_path / "CSVbot" / "sibot_settings.csv"
    _write(
        path,
        [
            {"chain_id": "*", "setting": "require_complete_history", "value": "true", "description": "history"},
            {"chain_id": "*", "setting": "min_closed_trades", "value": "50", "description": "trades"},
            {"chain_id": "*", "setting": "min_win_rate_pct", "value": "55", "description": "wins"},
            {"chain_id": "*", "setting": "history_candidate_wallets", "value": "40", "description": "candidates"},
        ],
    )

    compat._quality_compatible_relaxation(SimpleNamespace(), path)
    values = _read(path)

    assert values["require_complete_history"] == "false"
    assert values["min_closed_trades"] == "50"
    assert values["min_win_rate_pct"] == "55"
    assert values["history_candidate_wallets"] == "40"


def test_quality_compat_is_idempotent_when_already_false(tmp_path):
    path = tmp_path / "CSVbot" / "sibot_settings.csv"
    _write(
        path,
        [{"chain_id": "*", "setting": "require_complete_history", "value": "false", "description": "history"}],
    )
    before = path.read_text(encoding="utf-8")
    compat._quality_compatible_relaxation(SimpleNamespace(), path)
    assert path.read_text(encoding="utf-8") == before


def test_locked_ensure_applies_final_relaxation_after_old_migration(tmp_path, monkeypatch):
    path = tmp_path / "CSVbot" / "sibot_settings.csv"
    app = SimpleNamespace(data_dir=tmp_path / "data", csv_dir=tmp_path / "CSVbot")

    def fake_original_ensure(app_):
        # Simulate the old v1 quality migration being the last writer inside the
        # original chain: the stale value is true when control returns.
        _write(
            path,
            [{"chain_id": "*", "setting": "require_complete_history", "value": "true", "description": "history"}],
        )
        return path

    monkeypatch.setattr(runtime_compat, "_ORIGINAL_ENSURE", fake_original_ensure)
    monkeypatch.setattr(
        runtime_compat._reasonable,
        "_migrate_reasonable_defaults",
        compat._quality_compatible_relaxation,
    )

    result = runtime_compat._locked_ensure(app)
    assert result == path
    assert _read(path)["require_complete_history"] == "false"

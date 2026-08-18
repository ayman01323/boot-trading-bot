import subprocess
import sys
import textwrap


def _run(code: str):
    p = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], text=True, capture_output=True, timeout=60)
    assert p.returncode == 0, p.stdout + "\n" + p.stderr


def test_polygon_focus_is_off_by_default_and_filters_only_when_user_turns_it_on():
    _run(r'''
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from learnerbot import polygon_focus_patch as p

        with tempfile.TemporaryDirectory() as td:
            app=SimpleNamespace(csv_dir=Path(td))
            assert p.focus_enabled(app) is False
            calls=[]
            p._ORIGINAL_EXECUTE=lambda app, rows: calls.append(list(rows)) or [{"ok": True}]
            rows=[
                {"chain_id":56,"chain_slug":"bsc","route_id":"b"},
                {"chain_id":137,"chain_slug":"polygon","route_id":"p"},
                {"chain_id":8453,"chain_slug":"base","route_id":"x"},
            ]
            out=p._polygon_execute(app, rows)
            assert out == [{"ok": True}]
            assert [r["route_id"] for r in calls[-1]] == ["b","p","x"]

            p.set_focus(app, True)
            assert p.focus_enabled(app) is True
            calls.clear()
            out=p._polygon_execute(app, rows)
            assert out == [{"ok": True}]
            assert [r["route_id"] for r in calls[-1]] == ["p"]

            calls.clear()
            assert p._polygon_execute(app, [rows[0], rows[2]]) == []
            assert calls == []

            p.set_focus(app, False)
            assert p.focus_enabled(app) is False
    ''')


def test_polygon_focus_increases_coverage_without_lowering_quality_thresholds():
    _run(r'''
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from learnerbot import polygon_focus_patch as p

        with tempfile.TemporaryDirectory() as td:
            app=SimpleNamespace(csv_dir=Path(td))
            settings=app.csv_dir/"auto_trading_settings.csv"
            p._ORIGINAL_POWER_LOAD=lambda path, cid: {
                "fast_market_max_candidate_checks":"60",
                "fast_market_max_routes_per_pass":"20",
                "full_power_parallel_chains":"5",
                "max_price_impact_bps":"75",
                "direct_market_min_edge_base":"0.001",
            }
            off=p._power_settings(settings,0)
            assert off["fast_market_max_candidate_checks"] == "60"
            assert off["fast_market_max_routes_per_pass"] == "20"
            assert off["max_price_impact_bps"] == "75"
            assert off["direct_market_min_edge_base"] == "0.001"

            p.set_focus(app, True)
            on=p._power_settings(settings,0)
            assert int(on["fast_market_max_candidate_checks"]) >= 120
            assert int(on["fast_market_max_routes_per_pass"]) >= 60
            # Focus must not loosen economic/risk thresholds.
            assert on["max_price_impact_bps"] == "75"
            assert on["direct_market_min_edge_base"] == "0.001"
    ''')

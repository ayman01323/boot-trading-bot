from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import Journal, StrategyEngine, load_config, load_snapshots


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Isolated known-assets PAPER trading bot")
    p.add_argument("--config", default="config.json")
    p.add_argument("--db", default="state.sqlite3")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check"); sub.add_parser("list-assets")
    ev = sub.add_parser("evaluate"); ev.add_argument("--snapshot", required=True); ev.add_argument("--equity", type=float, default=10_000.0)
    run = sub.add_parser("run"); run.add_argument("--paper", action="store_true"); run.add_argument("--snapshots", required=True); run.add_argument("--equity", type=float, default=10_000.0)
    sub.add_parser("report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assets, risk, raw = load_config(args.config); journal = Journal(args.db); engine = StrategyEngine(assets, risk, journal)
    if args.command == "check":
        print(json.dumps({"status": "PASS", "mode": "PAPER_ONLY", "live_enabled": bool(raw.get("live", {}).get("enabled", False)),
                          "enabled_assets": [a.key for a in assets.values() if a.enabled], "db": str(Path(args.db).resolve())}, indent=2))
        if raw.get("live", {}).get("enabled", False):
            print("WARNING: config requests LIVE, but this MVP contains no live execution adapter."); return 2
        return 0
    if args.command == "list-assets":
        print(json.dumps([{"key": a.key, "chain": a.chain, "symbol": a.symbol, "address": a.address, "enabled": a.enabled} for a in assets.values()], indent=2)); return 0
    if args.command == "evaluate":
        snaps = load_snapshots(args.snapshot)
        if len(snaps) != 1: raise SystemExit("evaluate expects exactly one snapshot")
        print(json.dumps(engine.evaluate_entry(snaps[0], args.equity, now=snaps[0].ts).__dict__, indent=2)); return 0
    if args.command == "run":
        if not args.paper: raise SystemExit("Refusing to run: this testing bot requires --paper")
        equity = float(args.equity)
        for snap in load_snapshots(args.snapshots):
            p = engine.positions.get(snap.asset_key)
            if p:
                d = engine.evaluate_exit(p, snap, now=snap.ts)
                if d.action in {"EXIT", "EMERGENCY"}:
                    pnl = engine.close_paper(p, snap, d); equity += pnl
                    print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, "action": d.action, "reason": d.reason, "pnl_usd": pnl, "equity": equity}))
                else: print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, "action": "HOLD", "reason": d.reason}))
            else:
                d = engine.evaluate_entry(snap, equity, now=snap.ts)
                if d.action == "ENTER": engine.open_paper(snap, d)
                print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, **d.__dict__}))
        return 0
    if args.command == "report": print(json.dumps(journal.report(), indent=2)); return 0
    return 2


if __name__ == "__main__": raise SystemExit(main())

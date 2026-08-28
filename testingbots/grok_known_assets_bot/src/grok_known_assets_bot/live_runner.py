from __future__ import annotations

import argparse
import json
import signal
import time
from typing import Any

from .core import Journal, StrategyEngine, load_config
from .feed_safety import FeedSafetyError
from .live_feed import FeedWarmupError, SolanaNativeLiveFeed
from .research_adapter import assess_snapshot


def _research_payload(assessment: Any) -> dict[str, Any]:
    return {
        "canonical_asset_id": assessment.canonical_asset_id,
        "label": assessment.label,
        "confidence": assessment.confidence,
        "net_edge_pct": assessment.net_edge_pct,
        "estimated_cost_pct": assessment.estimated_cost_pct,
        "reasons": list(assessment.reasons),
        "features": dict(assessment.features),
    }


def _process_snapshot(
    engine: StrategyEngine,
    journal: Journal,
    snap,
    *,
    equity: float,
    min_confidence: float,
) -> float:
    position = engine.positions.get(snap.asset_key)
    if position:
        decision = engine.evaluate_exit(position, snap, now=snap.ts)
        if decision.action in {"EXIT", "EMERGENCY"}:
            pnl = engine.close_paper(position, snap, decision)
            equity += pnl
            print(json.dumps({
                "ts": snap.ts,
                "asset": snap.asset_key,
                "action": decision.action,
                "reason": decision.reason,
                "pnl_usd": pnl,
                "equity": equity,
                "paper": True,
                "feed": "real",
            }), flush=True)
        else:
            print(json.dumps({
                "ts": snap.ts,
                "asset": snap.asset_key,
                "action": "HOLD",
                "reason": decision.reason,
                "paper": True,
                "feed": "real",
            }), flush=True)
        return equity

    assessment = assess_snapshot(snap, engine.risk, now=snap.ts, min_confidence=min_confidence)
    journal.event("RESEARCH", snap.asset_key, _research_payload(assessment))
    if assessment.label == "REJECT":
        reason = "GROK_RESEARCH_REJECT:" + "|".join(assessment.reasons or ("LOW_COMPOSITE_CONFIDENCE",))
        journal.event("REJECT", snap.asset_key, {
            "reason": reason,
            "research_confidence": assessment.confidence,
            "paper": True,
            "feed": "real",
        })
        print(json.dumps({
            "ts": snap.ts,
            "asset": snap.asset_key,
            "action": "REJECT",
            "reason": reason,
            "research_confidence": assessment.confidence,
            "paper": True,
            "feed": "real",
        }), flush=True)
        return equity

    decision = engine.evaluate_entry(snap, equity, now=snap.ts)
    if decision.action == "ENTER":
        engine.open_paper(snap, decision)
    print(json.dumps({
        "ts": snap.ts,
        "asset": snap.asset_key,
        **decision.__dict__,
        "research_label": assessment.label,
        "research_confidence": assessment.confidence,
        "paper": True,
        "feed": "real",
    }), flush=True)
    return equity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent real-feed PAPER runner for Grok known-assets bot")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--db", default="state.sqlite3")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--research-min-confidence", type=float, default=0.60)
    parser.add_argument("--once", action="store_true", help="Run one collection cycle and exit; useful for diagnostics.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assets, risk, raw = load_config(args.config)
    if bool(raw.get("live", {}).get("enabled", False)):
        raise SystemExit("Refusing to start: real-feed runner is PAPER-only and config.live.enabled must remain false")

    journal = Journal(args.db)
    engine = StrategyEngine(assets, risk, journal)
    feed = SolanaNativeLiveFeed(assets, risk, journal, raw)
    supported = [asset for asset in assets.values() if feed.supported(asset)]
    unsupported = [asset.key for asset in assets.values() if asset.enabled and not feed.supported(asset)]
    if not supported:
        raise SystemExit("No supported enabled real-feed assets; enable native Solana SOL")

    journal.event("PAPER_RUNNER_START", None, {
        "supported_assets": [asset.key for asset in supported],
        "unsupported_enabled_assets": unsupported,
        "poll_seconds": feed.settings.poll_seconds,
        "paper": True,
        "feed": "real",
    })
    print(json.dumps({
        "status": "STARTED",
        "mode": "PAPER",
        "feed": "REAL_PUBLIC",
        "supported_assets": [asset.key for asset in supported],
        "unsupported_enabled_assets": unsupported,
        "poll_seconds": feed.settings.poll_seconds,
    }), flush=True)

    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    equity = float(args.equity)
    while running:
        cycle_started = time.time()
        for asset in supported:
            try:
                envelope = feed.collect(asset, now=time.time())
                snap = envelope.snapshot
                journal.event("REAL_FEED_SNAPSHOT", asset.key, {
                    "market_data_max_age_ms": envelope.market_data_max_age_ms,
                    "provider_disagreement_pct": envelope.provider_disagreement_pct,
                    "field_sources": dict(envelope.field_sources),
                    "paper": True,
                })
                equity = _process_snapshot(
                    engine,
                    journal,
                    snap,
                    equity=equity,
                    min_confidence=float(args.research_min_confidence),
                )
            except FeedWarmupError as exc:
                journal.event("FEED_WARMUP", asset.key, {"reason": str(exc), "paper": True})
                print(json.dumps({
                    "ts": time.time(),
                    "asset": asset.key,
                    "action": "WARMUP",
                    "reason": str(exc),
                    "paper": True,
                    "feed": "real",
                }), flush=True)
            except (FeedSafetyError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                journal.event("FEED_REJECT", asset.key, {"reason": f"{type(exc).__name__}:{exc}", "paper": True})
                print(json.dumps({
                    "ts": time.time(),
                    "asset": asset.key,
                    "action": "FEED_REJECT",
                    "reason": f"{type(exc).__name__}:{exc}",
                    "paper": True,
                    "feed": "real",
                }), flush=True)

        if args.once:
            break
        sleep_for = max(0.0, feed.settings.poll_seconds - (time.time() - cycle_started))
        if sleep_for > 0:
            time.sleep(sleep_for)

    journal.event("PAPER_RUNNER_STOP", None, {"equity": equity, "paper": True, "feed": "real"})
    print(json.dumps({"status": "STOPPED", "equity": equity, "paper": True, "feed": "real"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

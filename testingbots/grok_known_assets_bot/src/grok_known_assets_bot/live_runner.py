from __future__ import annotations

import argparse
import json
import signal
import time
from typing import Any

from .control import is_armed, load_state
from .core import Journal, StrategyEngine, load_config
from .feed_safety import FeedSafetyError
from .live_feed import FeedWarmupError, SolanaNativeLiveFeed
from .live_readiness import assess_live_readiness
from .position_state import restore_positions, sync_positions
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
    armed: bool,
    live_readiness: bool,
    feed_settings,
) -> float:
    mode = "LIVE_READINESS" if live_readiness else "PAPER_ONLY"
    position = engine.positions.get(snap.asset_key)
    if position:
        # Existing PAPER positions remain exitable even if readiness mode is later enabled.
        decision = engine.evaluate_exit(position, snap, now=snap.ts)
        if decision.action in {"EXIT", "EMERGENCY"}:
            pnl = engine.close_paper(position, snap, decision)
            sync_positions(journal, engine.positions)
            equity += pnl
            print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, "action": decision.action, "reason": decision.reason, "pnl_usd": pnl, "equity": equity, "armed": armed, "paper": True, "mode": mode, "feed": "real"}), flush=True)
        else:
            sync_positions(journal, engine.positions)
            print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, "action": "HOLD", "reason": decision.reason, "armed": armed, "paper": True, "mode": mode, "feed": "real"}), flush=True)
        return equity

    assessment = assess_snapshot(snap, engine.risk, now=snap.ts, min_confidence=min_confidence)
    journal.event("RESEARCH", snap.asset_key, _research_payload(assessment))
    if assessment.label == "REJECT":
        reason = "GROK_RESEARCH_REJECT:" + "|".join(assessment.reasons or ("LOW_COMPOSITE_CONFIDENCE",))
        journal.event("REJECT", snap.asset_key, {"reason": reason, "research_confidence": assessment.confidence, "armed": armed, "paper": not live_readiness, "mode": mode, "feed": "real"})
        print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, "action": "REJECT", "reason": reason, "research_confidence": assessment.confidence, "armed": armed, "paper": not live_readiness, "mode": mode, "feed": "real"}), flush=True)
        return equity

    if not armed:
        reason = "GROK_READINESS_DISARMED" if live_readiness else "GROK_PAPER_DISARMED"
        journal.event("REJECT", snap.asset_key, {"reason": reason, "research_confidence": assessment.confidence, "armed": False, "paper": not live_readiness, "mode": mode, "feed": "real"})
        print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, "action": "DISARMED", "reason": reason, "research_label": assessment.label, "research_confidence": assessment.confidence, "armed": False, "paper": not live_readiness, "mode": mode, "feed": "real"}), flush=True)
        return equity

    decision = engine.evaluate_entry(snap, equity, now=snap.ts)
    decision_payload = {
        **decision.__dict__,
        "research_label": assessment.label,
        "research_confidence": assessment.confidence,
        "armed": armed,
        "paper": not live_readiness,
        "mode": mode,
        "feed": "real",
    }
    journal.event("DECISION", snap.asset_key, decision_payload)

    if decision.action == "ENTER" and live_readiness:
        readiness = assess_live_readiness(snap, feed_settings, now=snap.ts)
        payload = {
            **readiness.payload(),
            "research_confidence": assessment.confidence,
            "strategy_reason": decision.reason,
            "strategy_size_usd": decision.size_usd,
            "mode": mode,
            "real_money_signing": False,
            "transaction_broadcast": False,
        }
        if readiness.ready:
            journal.event("LIVE_READY", snap.asset_key, payload)
            print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, "action": "LIVE_READY", **payload}), flush=True)
        else:
            journal.event("LIVE_PREFLIGHT_REJECT", snap.asset_key, payload)
            print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, "action": "LIVE_PREFLIGHT_REJECT", **payload}), flush=True)
        return equity

    if decision.action == "ENTER":
        engine.open_paper(snap, decision)
        sync_positions(journal, engine.positions)
    print(json.dumps({"ts": snap.ts, "asset": snap.asset_key, **decision_payload}), flush=True)
    return equity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent real-feed PAPER/LIVE-readiness runner for Grok known-assets bot")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--db", default="state.sqlite3")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--research-min-confidence", type=float, default=0.60)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assets, risk, raw = load_config(args.config)
    if bool(raw.get("live", {}).get("enabled", False)):
        raise SystemExit("Refusing to start: signing/broadcast execution is not part of the Grok readiness runner")
    journal = Journal(args.db)
    engine = StrategyEngine(assets, risk, journal)
    recovered = restore_positions(journal, assets)
    engine.positions.update(recovered)
    feed = SolanaNativeLiveFeed(assets, risk, journal, raw)
    supported = [asset for asset in assets.values() if feed.supported(asset)]
    unsupported = [asset.key for asset in assets.values() if asset.enabled and not feed.supported(asset)]
    if not supported:
        raise SystemExit("No supported enabled real-feed assets; enable native Solana SOL")
    state = load_state()
    live_readiness = bool(state.get("live_readiness_enabled"))
    mode = "LIVE_READINESS" if live_readiness else "PAPER_ONLY"
    journal.event("RUNNER_START", None, {"supported_assets": [a.key for a in supported], "unsupported_enabled_assets": unsupported, "poll_seconds": feed.settings.poll_seconds, "armed": bool(state.get("armed")), "recovered_positions": len(recovered), "mode": mode, "live_money_enabled": False, "feed": "real"})
    print(json.dumps({"status": "STARTED", "mode": mode, "feed": "REAL_PUBLIC", "armed": bool(state.get("armed")), "live_money_enabled": False, "signing_enabled": False, "broadcast_enabled": False, "recovered_positions": len(recovered), "supported_assets": [a.key for a in supported], "unsupported_enabled_assets": unsupported, "poll_seconds": feed.settings.poll_seconds}), flush=True)
    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    equity = float(args.equity)
    last_state = (bool(state.get("armed")), live_readiness)
    while running:
        cycle_started = time.time()
        current = load_state()
        armed = bool(current.get("armed"))
        live_readiness = bool(current.get("live_readiness_enabled"))
        if (armed, live_readiness) != last_state:
            mode = "LIVE_READINESS" if live_readiness else "PAPER_ONLY"
            journal.event("ARM_STATE_CHANGED", None, {"armed": armed, "mode": mode, "live_readiness_enabled": live_readiness, "live_money_enabled": False})
            print(json.dumps({"status": "ARM_STATE_CHANGED", "armed": armed, "mode": mode, "live_readiness_enabled": live_readiness, "live_money_enabled": False}), flush=True)
            last_state = (armed, live_readiness)
        for asset in supported:
            try:
                envelope = feed.collect(asset, now=time.time())
                snap = envelope.snapshot
                journal.event("REAL_FEED_SNAPSHOT", asset.key, {"market_data_max_age_ms": envelope.market_data_max_age_ms, "provider_disagreement_pct": envelope.provider_disagreement_pct, "field_sources": dict(envelope.field_sources), "armed": armed, "mode": "LIVE_READINESS" if live_readiness else "PAPER_ONLY"})
                equity = _process_snapshot(engine, journal, snap, equity=equity, min_confidence=float(args.research_min_confidence), armed=armed, live_readiness=live_readiness, feed_settings=feed.settings)
            except FeedWarmupError as exc:
                journal.event("FEED_WARMUP", asset.key, {"reason": str(exc), "armed": armed, "mode": "LIVE_READINESS" if live_readiness else "PAPER_ONLY"})
                print(json.dumps({"ts": time.time(), "asset": asset.key, "action": "WARMUP", "reason": str(exc), "armed": armed, "mode": "LIVE_READINESS" if live_readiness else "PAPER_ONLY", "feed": "real"}), flush=True)
            except (FeedSafetyError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                journal.event("FEED_REJECT", asset.key, {"reason": f"{type(exc).__name__}:{exc}", "armed": armed, "mode": "LIVE_READINESS" if live_readiness else "PAPER_ONLY"})
                print(json.dumps({"ts": time.time(), "asset": asset.key, "action": "FEED_REJECT", "reason": f"{type(exc).__name__}:{exc}", "armed": armed, "mode": "LIVE_READINESS" if live_readiness else "PAPER_ONLY", "feed": "real"}), flush=True)
        if args.once:
            break
        sleep_for = max(0.0, feed.settings.poll_seconds - (time.time() - cycle_started))
        if sleep_for > 0:
            time.sleep(sleep_for)
    sync_positions(journal, engine.positions)
    final_state = load_state()
    final_mode = "LIVE_READINESS" if final_state.get("live_readiness_enabled") else "PAPER_ONLY"
    journal.event("RUNNER_STOP", None, {"equity": equity, "armed": bool(final_state.get("armed")), "persisted_positions": len(engine.positions), "mode": final_mode, "live_money_enabled": False, "feed": "real"})
    print(json.dumps({"status": "STOPPED", "equity": equity, "armed": bool(final_state.get("armed")), "persisted_positions": len(engine.positions), "mode": final_mode, "live_money_enabled": False, "feed": "real"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

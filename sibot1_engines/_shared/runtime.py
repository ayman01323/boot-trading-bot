from __future__ import annotations

import csv
import json
import multiprocessing as mp
import os
import queue
import signal
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from .capital import CapitalManager
from .contracts import ExitIntent, MarketEvent, TradeIntent
from .market_data import MarketEvidenceBook, SharedBootMarketSource
from .paper_execution import MarketPriceBook, ShadowPaperExecutor
from .poolcheck_bridge import MandatoryShadowPoolCheck
from .positions import PositionManager
from .scoreboard import Scoreboard
from .worker import run_engine_worker


def _b(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class CapitalPools:
    """One virtual CapitalManager per physical chain/account context."""

    DEFAULTS = {
        "base": (Decimal("100"), {"gpt": Decimal("100")}),
        "solana": (Decimal("20"), {"gemini": Decimal("10"), "grok": Decimal("10")}),
    }

    def __init__(self, config_path: str | Path):
        rows = _rows(Path(config_path))
        self.managers: dict[str, CapitalManager] = {}
        if rows:
            for row in rows:
                chain = str(row.get("chain") or "").strip().lower()
                if not chain:
                    continue
                budget = Decimal(str(row.get("physical_paper_budget") or "0"))
                allocations: dict[str, Decimal] = {}
                for engine in ("gpt", "gemini", "grok"):
                    value = Decimal(str(row.get(f"{engine}_allocation") or "0"))
                    if value > 0:
                        allocations[engine] = value
                self.managers[chain] = CapitalManager(budget, allocations)
        if not self.managers:
            for chain, (budget, allocations) in self.DEFAULTS.items():
                self.managers[chain] = CapitalManager(budget, allocations)

    def for_intent(self, chain: str, engine_id: str) -> CapitalManager:
        manager = self.managers.get(str(chain).lower())
        if manager is None:
            raise KeyError(f"no capital pool for chain {chain}")
        # Snapshot also proves the engine has an account in this physical pool.
        manager.snapshot(engine_id)
        return manager

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for chain, manager in self.managers.items():
            accounts = {}
            for engine_id in ("gpt", "gemini", "grok"):
                try:
                    account = manager.snapshot(engine_id)
                except KeyError:
                    continue
                accounts[engine_id] = {
                    "cash": str(account.cash),
                    "reserved": str(account.reserved),
                    "invested_cost": str(account.invested_cost),
                    "realised_pnl": str(account.realised_pnl),
                }
            out[chain] = {"physical_paper_budget": str(manager.physical_budget), "accounts": accounts}
        return out


class SiBot1ShadowRuntime:
    ENGINE_IDS = ("gpt", "gemini", "grok")

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.csv_dir = self.root / "CSVbot"
        self.data_dir = self.root / "data"
        self.runtime_dir = self.data_dir / "sibot1"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        mode = str(os.environ.get("SIBOT1_EXECUTION_MODE", "SHADOW")).upper()
        if mode not in {"SHADOW", "PAPER"}:
            raise RuntimeError("SiBot1 runtime is hard-disabled for LIVE execution")
        self.mode = mode
        self.evidence = MarketEvidenceBook()
        self.market = SharedBootMarketSource(self.csv_dir, self.data_dir, self.evidence)
        self.prices = MarketPriceBook()
        self.poolcheck = MandatoryShadowPoolCheck(self.csv_dir, self.evidence)
        self.executor = ShadowPaperExecutor(self.prices, mode=mode)
        self.capital = CapitalPools(self.csv_dir / "sibot1" / "runtime.csv")
        self.positions = PositionManager()
        self.scoreboard = Scoreboard(self.runtime_dir)
        self._lot_ids: set[str] = set()
        self._lot_quote: dict[str, str] = {}
        self._last_open_poolcheck: dict[tuple[str, str], float] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._ctx = mp.get_context("fork")
        self._inboxes: dict[str, Any] = {}
        self._outbox = self._ctx.Queue(maxsize=1000)
        self._workers: dict[str, mp.Process] = {}
        self._stop = False
        self.status_path = self.runtime_dir / "status.json"
        self.poll_seconds = max(1.0, float(os.environ.get("SIBOT1_POLL_SECONDS", "5")))

    def _registry(self) -> list[dict[str, str]]:
        path = self.csv_dir / "sibot1" / "engine_registry.csv"
        rows = _rows(path)
        if rows:
            return rows
        return [
            {"engine_id": engine, "enabled": "1", "settings_path": f"CSVbot/sibot1/engines/{engine}/settings.example.csv"}
            for engine in self.ENGINE_IDS
        ]

    def _settings_path(self, engine_id: str, raw: str) -> Path:
        value = str(raw or "").strip()
        if value:
            path = Path(value)
            if not path.is_absolute():
                path = self.root / path
            if path.exists():
                return path
        active = self.csv_dir / "sibot1" / "engines" / engine_id / "settings.csv"
        if active.exists():
            return active
        return self.csv_dir / "sibot1" / "engines" / engine_id / "settings.example.csv"

    def start_workers(self) -> None:
        for row in self._registry():
            engine_id = str(row.get("engine_id") or "").strip().lower()
            if engine_id not in self.ENGINE_IDS or not _b(row.get("enabled"), True):
                continue
            settings = self._settings_path(engine_id, str(row.get("settings_path") or ""))
            inbox = self._ctx.Queue(maxsize=500)
            process = self._ctx.Process(
                target=run_engine_worker,
                args=(engine_id, str(settings), str(self.runtime_dir), inbox, self._outbox),
                name=f"sibot1-{engine_id}",
                daemon=True,
            )
            process.start()
            self._inboxes[engine_id] = inbox
            self._workers[engine_id] = process
            self._health[engine_id] = {"state": "STARTING", "pid": process.pid, "settings": str(settings)}

    def stop_workers(self) -> None:
        for inbox in self._inboxes.values():
            try:
                inbox.put_nowait(("STOP", None))
            except Exception:
                pass
        deadline = time.time() + 3
        for process in self._workers.values():
            process.join(max(0, deadline - time.time()))
            if process.is_alive():
                process.terminate()

    def halted(self) -> bool:
        return _b(os.environ.get("SIBOT1_MASTER_HALT"), False) or (self.runtime_dir / "MASTER_HALT").exists()

    def _safe_send(self, engine_id: str, kind: str, payload: Any) -> None:
        inbox = self._inboxes.get(engine_id)
        if inbox is None:
            return
        try:
            inbox.put_nowait((kind, payload))
        except queue.Full:
            self.scoreboard.error(engine_id, getattr(payload, "chain", "runtime"), "worker inbox full")

    def _broadcast_market(self, event: MarketEvent) -> None:
        self.prices.observe(event)
        for engine_id in self._inboxes:
            self._safe_send(engine_id, "MARKET", event)
        self._send_position_updates(event)
        self._open_position_poolcheck(event)

    def _send_position_updates(self, event: MarketEvent) -> None:
        if event.price is None or not event.asset_out:
            return
        for lot_id in tuple(self._lot_ids):
            try:
                lot = self.positions.get(lot_id)
            except KeyError:
                self._lot_ids.discard(lot_id)
                continue
            if lot.remaining_quantity <= 0:
                self._lot_ids.discard(lot_id)
                continue
            if lot.chain.lower() != event.chain.lower() or lot.asset != event.asset_out:
                continue
            current_value = lot.remaining_quantity * Decimal(event.price)
            pnl_pct = (
                (current_value - lot.remaining_cost_basis) / lot.remaining_cost_basis
                if lot.remaining_cost_basis > 0 else Decimal("0")
            )
            update = {
                "engine_id": lot.engine_id,
                "lot_id": lot.lot_id,
                "chain": lot.chain,
                "asset": lot.asset,
                "remaining_quantity": str(lot.remaining_quantity),
                "remaining_cost_basis": str(lot.remaining_cost_basis),
                "current_value": str(current_value),
                "pnl_pct": str(pnl_pct),
                "age_ms": max(0, event.observed_at_ms - lot.entry_at_ms),
                "observed_at_ms": event.observed_at_ms,
                "trend_reversal": False,
            }
            self._safe_send(lot.engine_id, "POSITION", update)

    def _open_position_poolcheck(self, event: MarketEvent) -> None:
        if not event.asset_out:
            return
        key = (event.chain.lower(), event.asset_out)
        now = time.monotonic()
        if now - self._last_open_poolcheck.get(key, 0) < 60:
            return
        matching = []
        for lot_id in tuple(self._lot_ids):
            try:
                lot = self.positions.get(lot_id)
            except KeyError:
                continue
            if lot.remaining_quantity > 0 and lot.chain.lower() == key[0] and lot.asset == key[1]:
                matching.append(lot)
        if not matching:
            return
        self._last_open_poolcheck[key] = now
        decision = self.poolcheck.assess_open_position(chain=event.chain, asset=event.asset_out)
        if decision.verdict.upper() != "HARD_BLOCK":
            return
        for lot in matching:
            emergency = ExitIntent(
                intent_id=f"poolcheck-emergency-{lot.lot_id}-{int(time.time()*1000)}",
                engine_id=lot.engine_id,
                engine_version=lot.engine_version,
                strategy_id=lot.strategy_id,
                chain=lot.chain,
                created_at_ms=event.observed_at_ms,
                lot_id=lot.lot_id,
                asset=lot.asset,
                exit_fraction=Decimal("1"),
                reason="POOLCHECK_HARD_BLOCK_EMERGENCY_SHADOW_EXIT",
                metadata={"central_poolcheck": True, "verdict": decision.verdict, "reasons": decision.reasons},
            )
            self._handle_exit(emergency)

    def _handle_trade(self, intent: TradeIntent) -> None:
        self.scoreboard.signal(intent.engine_id, intent.chain, intent.intent_id)
        if self.halted():
            self.scoreboard.audit("MASTER_HALT_REJECT", engine_id=intent.engine_id, chain=intent.chain, intent_id=intent.intent_id)
            return
        try:
            manager = self.capital.for_intent(intent.chain, intent.engine_id)
            reservation = manager.reserve(intent.engine_id, intent.requested_input_amount)
        except Exception as exc:
            self.scoreboard.error(intent.engine_id, intent.chain, f"capital reserve rejected: {type(exc).__name__}: {exc}")
            return
        decision = self.poolcheck.assess_entry(intent)
        self.scoreboard.poolcheck(intent.engine_id, intent.chain, decision.verdict, decision.reasons)
        if decision.verdict.upper() not in {"PASS", "SHADOW_ONLY"}:
            manager.release(reservation.reservation_id)
            return
        receipt = self.executor.execute_entry(intent, reservation.reservation_id)
        if not receipt.status.startswith("PAPER_") or "REJECTED" in receipt.status:
            manager.release(reservation.reservation_id)
            self.scoreboard.error(intent.engine_id, intent.chain, f"paper entry not filled: {receipt.status}")
            return
        actual = manager.commit_entry(reservation.reservation_id, receipt.actual_input_cost)
        if intent.side.upper() == "ARBITRAGE":
            pnl = manager.settle_exit(intent.engine_id, actual, receipt.proceeds)
            self.scoreboard.entry(intent.engine_id, intent.chain, tx_id=receipt.tx_id)
            self.scoreboard.exit(intent.engine_id, intent.chain, tx_id=receipt.tx_id, pnl=pnl)
            return
        if receipt.acquired_quantity <= 0 or not receipt.acquired_asset:
            # Deterministic paper executor should not reach this branch after a fill.
            manager.settle_exit(intent.engine_id, actual, actual)
            self.scoreboard.error(intent.engine_id, intent.chain, "paper fill returned no acquired quantity")
            return
        lot = self.positions.open_lot(
            engine_id=intent.engine_id,
            engine_version=intent.engine_version,
            strategy_id=intent.strategy_id,
            chain=intent.chain,
            asset=receipt.acquired_asset,
            quantity=receipt.acquired_quantity,
            cost_basis=actual,
            entry_tx=receipt.tx_id,
            entry_at_ms=int(intent.created_at_ms),
        )
        self._lot_ids.add(lot.lot_id)
        self._lot_quote[lot.lot_id] = str(receipt.metadata.get("quote_asset") or intent.asset_in)
        self.scoreboard.entry(intent.engine_id, intent.chain, tx_id=receipt.tx_id, lot_id=lot.lot_id)

    def _handle_exit(self, intent: ExitIntent) -> None:
        if not intent.lot_id:
            self.scoreboard.error(intent.engine_id, intent.chain, "asset-wide exits are not enabled in v1; lot_id required")
            return
        try:
            lot = self.positions.get(intent.lot_id)
            planned = self.positions.plan_exit(
                engine_id=intent.engine_id,
                lot_id=intent.lot_id,
                quantity=intent.requested_quantity,
                fraction=intent.exit_fraction,
            )
        except Exception as exc:
            self.scoreboard.error(intent.engine_id, intent.chain, f"exit ownership/size rejected: {type(exc).__name__}: {exc}")
            return
        resolved = replace(intent, asset=lot.asset)
        receipt = self.executor.execute_exit(resolved, quantity=planned.quantity)
        if receipt.status != "PAPER_FILLED":
            self.scoreboard.error(intent.engine_id, intent.chain, f"paper exit not filled: {receipt.status}")
            return
        self.positions.apply_exit(planned)
        try:
            manager = self.capital.for_intent(lot.chain, lot.engine_id)
            pnl = manager.settle_exit(lot.engine_id, planned.cost_basis, receipt.proceeds)
        except Exception as exc:
            self.scoreboard.error(intent.engine_id, intent.chain, f"paper settlement failed: {type(exc).__name__}: {exc}")
            return
        self.scoreboard.exit(intent.engine_id, intent.chain, tx_id=receipt.tx_id, pnl=pnl)
        if self.positions.get(lot.lot_id).remaining_quantity <= 0:
            self._lot_ids.discard(lot.lot_id)
            self._lot_quote.pop(lot.lot_id, None)

    def _drain_workers(self) -> None:
        while True:
            try:
                kind, engine_id, payload = self._outbox.get_nowait()
            except queue.Empty:
                break
            if kind in {"READY", "HEALTH", "STOPPED"}:
                self._health[engine_id] = {"state": kind, **dict(payload or {})}
            elif kind in {"ERROR", "FATAL"}:
                self._health[engine_id] = {"state": kind, **dict(payload or {})}
                self.scoreboard.error(engine_id, "runtime", str((payload or {}).get("error") or kind))
            elif kind == "INTENT" and isinstance(payload, TradeIntent):
                self._handle_trade(payload)
            elif kind == "INTENT" and isinstance(payload, ExitIntent):
                self._handle_exit(payload)

    def _status(self) -> dict[str, Any]:
        workers = {}
        for engine_id, process in self._workers.items():
            workers[engine_id] = {
                **dict(self._health.get(engine_id, {})),
                "pid": process.pid,
                "alive": process.is_alive(),
            }
        return {
            "schema_version": 1,
            "state": "HALTED" if self.halted() else "ACTIVE",
            "mode": self.mode,
            "live_enabled": False,
            "signer_attached": False,
            "broadcast_enabled": False,
            "wallet_private_key_access": False,
            "root": str(self.root),
            "pid": os.getpid(),
            "updated_epoch": int(time.time()),
            "workers": workers,
            "capital": self.capital.snapshot(),
            "open_lots": len(self._lot_ids),
            "scoreboard": self.scoreboard.snapshot(),
        }

    def _write_status(self) -> None:
        payload = self._status()
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.status_path)
        self.scoreboard.flush()

    def run_once(self) -> int:
        events = self.market.poll()
        for event in events:
            self._broadcast_market(event)
        # Give child processes a short opportunity to answer without serialising them.
        deadline = time.monotonic() + 0.20
        while time.monotonic() < deadline:
            self._drain_workers()
            time.sleep(0.01)
        self._drain_workers()
        self._write_status()
        return len(events)

    def request_stop(self, *_args: Any) -> None:
        self._stop = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        self.start_workers()
        self.scoreboard.audit(
            "RUNTIME_START",
            mode=self.mode,
            live_enabled=False,
            signer_attached=False,
            broadcast_enabled=False,
            engines=list(self._workers),
        )
        try:
            while not self._stop:
                started = time.monotonic()
                try:
                    self.run_once()
                except Exception as exc:
                    self.scoreboard.error("controller", "runtime", f"cycle error: {type(exc).__name__}: {exc}")
                    self._write_status()
                delay = max(0.1, self.poll_seconds - (time.monotonic() - started))
                time.sleep(delay)
        finally:
            self.stop_workers()
            self.scoreboard.audit("RUNTIME_STOP", mode=self.mode)
            self._write_status()


def run_shadow_runtime(root: str | Path) -> None:
    SiBot1ShadowRuntime(root).run_forever()

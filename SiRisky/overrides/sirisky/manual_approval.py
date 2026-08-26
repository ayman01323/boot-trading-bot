from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from pathlib import Path

from .csvio import as_bool
from .jupiter import order as jup_order, WSOL_MINT
from .wallet import WalletStore

HEADERS = [
    "created_epoch",
    "expires_epoch",
    "order_id",
    "action",
    "pool_id",
    "mint",
    "amount_raw",
    "amount_display",
    "expected_output_raw",
    "min_output_raw",
    "slippage_bps",
    "price_impact_pct",
    "estimated_fee_lamports",
    "exit_health_pct",
    "strategy_id",
    "opportunity_id",
    "route_summary",
    "proposal_hash",
    "status",
]

HASH_FIELDS = [
    "created_epoch",
    "expires_epoch",
    "order_id",
    "action",
    "pool_id",
    "mint",
    "amount_raw",
    "expected_output_raw",
    "min_output_raw",
    "slippage_bps",
    "price_impact_pct",
    "estimated_fee_lamports",
    "exit_health_pct",
    "strategy_id",
    "opportunity_id",
    "route_summary",
]


class ManualApprovalGate:
    """Prepare immutable per-trade proposals without signing or broadcasting.

    This gate intentionally has no approve-and-send method. A proposal stops at
    WAITING_FOR_MANUAL_APPROVAL and requires an external/manual signature path.
    """

    def __init__(self, settings):
        self.settings = settings
        self.path = Path(settings.csv_dir) / "pending_approvals.csv"

    def enabled(self) -> bool:
        return as_bool(self.settings.runtime().get("manual_approval_enabled"), False)

    def external_signature_required(self) -> bool:
        return as_bool(
            self.settings.runtime().get("manual_approval_require_external_signature"),
            True,
        )

    def ttl_seconds(self) -> int:
        try:
            ttl = int(float(self.settings.runtime().get("manual_approval_ttl_seconds") or 60))
        except Exception:
            ttl = 60
        return max(15, min(300, ttl))

    @staticmethod
    def proposal_hash(row: dict) -> str:
        payload = {k: str(row.get(k) or "") for k in HASH_FIELDS}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def _write(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({h: row.get(h, "") for h in HEADERS})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    @staticmethod
    def _expire_rows(rows: list[dict], now: int) -> bool:
        changed = False
        for row in rows:
            if str(row.get("status") or "").upper() != "WAITING_FOR_MANUAL_APPROVAL":
                continue
            try:
                expires = int(float(row.get("expires_epoch") or 0))
            except Exception:
                expires = 0
            if expires <= now:
                row["status"] = "EXPIRED"
                changed = True
        return changed

    def pending(self) -> list[dict]:
        rows = self._load()
        changed = self._expire_rows(rows, int(time.time()))
        if changed:
            self._write(rows)
        return [
            r for r in rows
            if str(r.get("status") or "").upper() == "WAITING_FOR_MANUAL_APPROVAL"
        ]

    @staticmethod
    def _route_summary(q: dict) -> str:
        summary = {
            "router": q.get("router") or q.get("routerName") or "",
            "swap_type": q.get("swapType") or q.get("mode") or "",
        }
        route = q.get("routePlan")
        if route:
            summary["route"] = route
        text = json.dumps(summary, sort_keys=True, separators=(",", ":"), default=str)
        return text[:700]

    @staticmethod
    def _fee_lamports(q: dict) -> int:
        total = 0
        for key in (
            "signatureFeeLamports",
            "prioritizationFeeLamports",
            "rentFeeLamports",
            "platformFeeLamports",
        ):
            try:
                total += int(q.get(key) or 0)
            except Exception:
                pass
        return total

    @staticmethod
    def _out_amount(q: dict) -> int:
        for key in ("outAmount", "outputAmount", "estimatedOutputAmount", "out_amount"):
            try:
                value = int(q.get(key) or 0)
            except Exception:
                value = 0
            if value > 0:
                return value
        return 0

    @staticmethod
    def _min_out(q: dict) -> int:
        for key in (
            "otherAmountThreshold",
            "minimumOutputAmount",
            "minOutAmount",
            "min_output_amount",
        ):
            try:
                value = int(q.get(key) or 0)
            except Exception:
                value = 0
            if value > 0:
                return value
        return 0

    def prepare(self, order, context: dict | None = None) -> dict:
        """Build a Jupiter order proposal, never sign or broadcast it."""
        context = context or {}
        now = int(time.time())
        rows = self._load()
        changed = self._expire_rows(rows, now)

        # Reuse an active proposal for the same action/mint so the 5-second
        # engine loop cannot create or notify a new proposal every cycle.
        for row in reversed(rows):
            if str(row.get("status") or "").upper() != "WAITING_FOR_MANUAL_APPROVAL":
                continue
            if str(row.get("action") or "").upper() != str(order.action).upper():
                continue
            if str(row.get("mint") or "") != str(order.mint):
                continue
            if changed:
                self._write(rows)
            return {"proposal": dict(row), "created": False}

        taker = WalletStore(self.settings).address()
        if str(order.action).upper() == "BUY":
            q = jup_order(self.settings, taker, WSOL_MINT, order.mint, int(order.amount_raw))
        else:
            q = jup_order(self.settings, taker, order.mint, WSOL_MINT, int(order.amount_raw))

        expires = now + self.ttl_seconds()
        action = str(order.action).upper()
        amount_display = (
            f"{int(order.amount_raw) / 1_000_000_000:.9f} SOL"
            if action == "BUY"
            else str(int(order.amount_raw)) + " raw token units"
        )
        row = {
            "created_epoch": str(now),
            "expires_epoch": str(expires),
            "order_id": str(order.order_id),
            "action": action,
            "pool_id": str(context.get("pool_id") or ""),
            "mint": str(order.mint),
            "amount_raw": str(int(order.amount_raw)),
            "amount_display": amount_display,
            "expected_output_raw": str(self._out_amount(q)),
            "min_output_raw": str(self._min_out(q)),
            "slippage_bps": str(q.get("slippageBps") or context.get("slippage_bps") or ""),
            "price_impact_pct": str(q.get("priceImpactPct") or q.get("price_impact_pct") or ""),
            "estimated_fee_lamports": str(self._fee_lamports(q)),
            "exit_health_pct": str(context.get("exit_health_pct") or ""),
            "strategy_id": str(context.get("strategy_id") or ""),
            "opportunity_id": str(order.opportunity_id or ""),
            "route_summary": self._route_summary(q),
            "proposal_hash": "",
            "status": "WAITING_FOR_MANUAL_APPROVAL",
        }
        row["proposal_hash"] = self.proposal_hash(row)
        rows.append(row)
        self._write(rows)
        return {"proposal": dict(row), "created": True}

    @staticmethod
    def format_for_user(row: dict) -> str:
        now = int(time.time())
        try:
            seconds = max(0, int(float(row.get("expires_epoch") or 0)) - now)
        except Exception:
            seconds = 0
        return (
            "SiRisky — WAITING FOR MANUAL APPROVAL\n"
            f"Action: {row.get('action','')}\n"
            f"Order: {row.get('order_id','')}\n"
            f"Pool: {row.get('pool_id','')}\n"
            f"Mint: {row.get('mint','')}\n"
            f"Amount: {row.get('amount_display','')}\n"
            f"Expected output (raw): {row.get('expected_output_raw','')}\n"
            f"Minimum output (raw): {row.get('min_output_raw','')}\n"
            f"Slippage (bps): {row.get('slippage_bps','')}\n"
            f"Price impact (%): {row.get('price_impact_pct','')}\n"
            f"Estimated fees (lamports): {row.get('estimated_fee_lamports','')}\n"
            f"Exit health (%): {row.get('exit_health_pct','')}\n"
            f"Proposal hash: {row.get('proposal_hash','')}\n"
            f"Expires in: {seconds}s\n"
            "External/manual signature required. Server-side transaction broadcast remains locked."
        )

# Deployment trigger: armed automatic candidate evaluation with manual signing.

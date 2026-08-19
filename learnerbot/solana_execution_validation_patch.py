from __future__ import annotations

import time
from decimal import Decimal

from . import solana_live_executor as _exec
from . import solana_sibot as _sol

# This patch is installed after the position-wallet binding layer. Jupiter's
# documented economic amount fields remain mandatory, while swapEvents are kept
# as diagnostics rather than treated as proof of execution by themselves.
_PREV_SWAP = _exec.SolanaLiveExecutor.swap
_PREV_BUY = _exec.SolanaLiveExecutor.buy
_PREV_SELL = _exec.SolanaLiveExecutor.sell


class SolanaLiveReconciliationPending(_exec.SolanaLiveError):
    """A Jupiter-success transaction needs chain reconciliation before any retry."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = dict(result or {})
        self.signature = str(self.result.get("signature") or "")


def _amount(value) -> int:
    try:
        return int(str(value))
    except Exception:
        return 0


def _economic_amounts(result: dict) -> tuple[int, int]:
    result = dict(result or {})
    inp = _amount(result.get("totalInputAmount") or result.get("inputAmountResult"))
    out = _amount(result.get("totalOutputAmount") or result.get("outputAmountResult"))
    return inp, out


def _only_missing_swap_events(exc: Exception) -> bool:
    text = str(exc)
    if "missing swapEvents" not in text:
        return False
    if "non-positive executed input" in text or "non-positive executed output" in text:
        return False
    result = dict(getattr(exc, "result", {}) or {})
    inp, out = _economic_amounts(result)
    return inp > 0 and out > 0 and bool(result.get("signature"))


def _swap_amounts_authoritative(self, input_mint, output_mint, amount_raw):
    try:
        result = _PREV_SWAP(self, input_mint, output_mint, amount_raw)
    except _exec.SolanaLivePostExecutionError as exc:
        # Jupiter documents totalInputAmount as the amount deducted from the
        # user's wallet and totalOutputAmount as the final output reflected in
        # the user's wallet. Missing swapEvents alone must therefore not turn a
        # positive executed swap into a landed-invalid fault.
        if not _only_missing_swap_events(exc):
            raise
        result = dict(exc.result or {})
        result["swap_events_present"] = False
        result["economic_validation"] = "POSITIVE_AMOUNTS_EVENTS_MISSING"
        return result
    result = dict(result or {})
    result["swap_events_present"] = bool(result.get("swapEvents") or [])
    return result


def _tx_evidence(self, signature: str, mint: str) -> dict:
    """Poll the landed signature and derive wallet deltas from transaction metadata."""
    signature = str(signature or "")
    if not signature:
        return {"visible": False, "error": "missing signature"}

    cfg = _sol.settings(self.app)
    attempts = max(1, min(12, _sol._int(cfg.get("live_reconcile_tx_attempts"), 8)))
    delay = max(0.20, min(3.0, _sol._float(cfg.get("live_reconcile_tx_delay_seconds"), 0.75)))
    last_error = ""

    for attempt in range(attempts):
        try:
            tx = _sol._rpc(
                self.app,
                "getTransaction",
                [
                    signature,
                    {
                        "commitment": "confirmed",
                        "maxSupportedTransactionVersion": 0,
                        "encoding": "jsonParsed",
                    },
                ],
            )
        except Exception as exc:
            tx = None
            last_error = f"{type(exc).__name__}: {exc}"

        if tx:
            meta = tx.get("meta") or {}
            if meta.get("err") is not None:
                return {
                    "visible": True,
                    "tx_ok": False,
                    "tx_error": str(meta.get("err")),
                    "token_delta_raw": 0,
                    "wallet_delta_lamports": None,
                }

            deltas, _pre, _post, _decimals = _sol._token_state(tx, self.address)
            token_delta = int(deltas.get(str(mint), 0))
            wallet_delta_lamports = None
            try:
                sol_delta = _sol._sol_delta(tx, self.address)
                wallet_delta_lamports = int(
                    (Decimal(sol_delta) * Decimal(1_000_000_000)).to_integral_value()
                )
            except Exception:
                wallet_delta_lamports = None

            return {
                "visible": True,
                "tx_ok": True,
                "tx_error": "",
                "token_delta_raw": token_delta,
                "wallet_delta_lamports": wallet_delta_lamports,
                "slot": int(tx.get("slot") or 0),
            }

        if attempt + 1 < attempts:
            time.sleep(delay)

    return {"visible": False, "error": last_error or "transaction not yet visible"}


def _apply_chain_evidence(result: dict, evidence: dict) -> None:
    result["chain_tx_visible"] = bool(evidence.get("visible"))
    result["chain_tx_reconciled"] = bool(evidence.get("visible") and evidence.get("tx_ok"))
    result["chain_tx_error"] = str(evidence.get("tx_error") or evidence.get("error") or "")
    result["chain_token_delta_raw"] = int(evidence.get("token_delta_raw") or 0)
    if evidence.get("slot"):
        result["chain_tx_slot"] = int(evidence["slot"])
    if evidence.get("wallet_delta_lamports") is not None:
        # Replace the immediate post-execute wallet snapshot with the authoritative
        # transaction-level lamport movement. This prevents stale RPC reads from
        # corrupting realised P&L.
        result["wallet_delta_lamports"] = int(evidence["wallet_delta_lamports"])
        result["wallet_balance_reconciled"] = True
        result["wallet_reconciliation_source"] = "getTransaction"


def _buy_with_token_reconciliation(self, output_mint, amount_sol, reserve_sol):
    before = None
    try:
        before = int(self.token_balance_raw(output_mint))
    except Exception:
        before = None

    result = dict(_PREV_BUY(self, output_mint, amount_sol, reserve_sol) or {})
    evidence = _tx_evidence(self, result.get("signature"), output_mint)
    _apply_chain_evidence(result, evidence)

    after = None
    try:
        after = int(self.token_balance_raw(output_mint))
    except Exception:
        after = None

    result["output_token_before_raw"] = before
    result["output_token_after_raw"] = after
    result["output_token_balance_reconciled"] = before is not None and after is not None

    if evidence.get("visible"):
        if not evidence.get("tx_ok"):
            raise _exec.SolanaLivePostExecutionError(
                "Jupiter transaction reported Success but the BUY transaction failed on chain",
                result,
            )
        delta = int(evidence.get("token_delta_raw") or 0)
        result["output_token_delta_raw"] = delta
        result["output_token_reconciliation_source"] = "getTransaction"
        if delta <= 0:
            raise _exec.SolanaLivePostExecutionError(
                "Jupiter transaction reported Success but BUY produced no on-chain output-token increase",
                result,
            )
        return result

    # If transaction metadata is temporarily unavailable, retain the previous
    # balance-delta fallback for BUYs. SELLs are handled more strictly below
    # because a blind retry can double-execute an exit.
    if before is not None and after is not None:
        delta = after - before
        result["output_token_delta_raw"] = delta
        result["output_token_reconciliation_source"] = "wallet_balance_fallback"
        if delta <= 0:
            raise _exec.SolanaLivePostExecutionError(
                "Jupiter transaction reported Success but BUY produced no output-token balance increase",
                result,
            )
    return result


def _sell_with_token_reconciliation(self, input_mint, amount_raw):
    before = None
    try:
        before = int(self.token_balance_raw(input_mint))
    except Exception:
        before = None

    result = dict(_PREV_SELL(self, input_mint, amount_raw) or {})
    result["requested_sell_raw"] = int(amount_raw)

    # A Jupiter "Success" response is not allowed to become POSITION_SOLD merely
    # because a second wallet-balance RPC happens to look right. Reconcile the
    # exact signature against transaction metadata first.
    evidence = _tx_evidence(self, result.get("signature"), input_mint)
    _apply_chain_evidence(result, evidence)

    after = None
    try:
        after = int(self.token_balance_raw(input_mint))
    except Exception:
        after = None

    result["input_token_before_raw"] = before
    result["input_token_after_raw"] = after
    result["input_token_balance_reconciled"] = before is not None and after is not None

    if evidence.get("visible"):
        if not evidence.get("tx_ok"):
            raise _exec.SolanaLivePostExecutionError(
                "Jupiter transaction reported Success but the SELL transaction failed on chain",
                result,
            )
        token_delta = int(evidence.get("token_delta_raw") or 0)
        sold_raw = -token_delta
        result["input_token_delta_raw"] = sold_raw
        result["input_token_reconciliation_source"] = "getTransaction"
        if sold_raw <= 0:
            raise _exec.SolanaLivePostExecutionError(
                "Jupiter transaction reported Success but SELL produced no on-chain input-token decrease",
                result,
            )
        return result

    # Do not call this landed-invalid and do not broadcast a second SELL. The
    # transaction may simply not be visible on this RPC yet. The outer exit
    # circuit stores the signature and an idempotent reconciler checks it later.
    if before is not None and after is not None:
        result["input_token_delta_raw"] = before - after
        result["input_token_reconciliation_source"] = "untrusted_post_balance"
    raise SolanaLiveReconciliationPending(
        "Jupiter reported Success but SELL transaction metadata is not yet visible; reconciliation required before any retry",
        result,
    )


def install():
    if getattr(_exec.SolanaLiveExecutor, "_economic_validation_patch", False):
        return
    # Keep the configuration field for backwards-compatible reporting, but no
    # execution path treats an absent swapEvents array as a fault by itself.
    _sol.DEFAULTS["live_require_swap_events"] = (
        "false",
        "Record Jupiter swapEvents when available; positive executed amounts and wallet token movement are authoritative",
    )
    _sol.DEFAULTS["live_reconcile_tx_attempts"] = (
        "8",
        "Number of confirmed getTransaction checks after Jupiter reports Success",
    )
    _sol.DEFAULTS["live_reconcile_tx_delay_seconds"] = (
        "0.75",
        "Delay between post-Jupiter transaction reconciliation checks",
    )
    _exec.SolanaLiveExecutor.swap = _swap_amounts_authoritative
    _exec.SolanaLiveExecutor.buy = _buy_with_token_reconciliation
    _exec.SolanaLiveExecutor.sell = _sell_with_token_reconciliation
    _exec.SolanaLiveExecutor._economic_validation_patch = True


install()

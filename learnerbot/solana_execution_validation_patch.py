from __future__ import annotations

from . import solana_live_executor as _exec
from . import solana_sibot as _sol

# This patch is installed after the position-wallet binding layer.  Jupiter's
# documented economic amount fields remain mandatory, while swapEvents are kept
# as diagnostics rather than treated as proof of execution by themselves.
_PREV_SWAP = _exec.SolanaLiveExecutor.swap
_PREV_BUY = _exec.SolanaLiveExecutor.buy
_PREV_SELL = _exec.SolanaLiveExecutor.sell


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
        # the user's wallet.  Missing swapEvents alone must therefore not turn a
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


def _buy_with_token_reconciliation(self, output_mint, amount_sol, reserve_sol):
    before = None
    try:
        before = int(self.token_balance_raw(output_mint))
    except Exception:
        before = None

    result = dict(_PREV_BUY(self, output_mint, amount_sol, reserve_sol) or {})

    after = None
    try:
        after = int(self.token_balance_raw(output_mint))
    except Exception:
        after = None

    result["output_token_before_raw"] = before
    result["output_token_after_raw"] = after
    result["output_token_balance_reconciled"] = before is not None and after is not None
    if before is not None and after is not None:
        delta = after - before
        result["output_token_delta_raw"] = delta
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

    after = None
    try:
        after = int(self.token_balance_raw(input_mint))
    except Exception:
        after = None

    result["input_token_before_raw"] = before
    result["input_token_after_raw"] = after
    result["input_token_balance_reconciled"] = before is not None and after is not None
    if before is not None and after is not None:
        delta = before - after
        result["input_token_delta_raw"] = delta
        if delta <= 0:
            raise _exec.SolanaLivePostExecutionError(
                "Jupiter transaction reported Success but SELL produced no input-token balance decrease",
                result,
            )
    return result


def install():
    if getattr(_exec.SolanaLiveExecutor, "_economic_validation_patch", False):
        return
    # Keep the configuration field for backwards-compatible reporting, but no
    # execution path treats an absent swapEvents array as a fault by itself.
    _sol.DEFAULTS["live_require_swap_events"] = (
        "false",
        "Record Jupiter swapEvents when available; positive executed amounts and wallet token movement are authoritative",
    )
    _exec.SolanaLiveExecutor.swap = _swap_amounts_authoritative
    _exec.SolanaLiveExecutor.buy = _buy_with_token_reconciliation
    _exec.SolanaLiveExecutor.sell = _sell_with_token_reconciliation
    _exec.SolanaLiveExecutor._economic_validation_patch = True


install()

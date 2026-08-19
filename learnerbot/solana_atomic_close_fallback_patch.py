from __future__ import annotations

from . import solana_execution_efficiency_patch as _eff
from . import solana_jupiter_order_recovery_patch as _jup_recovery  # noqa: F401
from . import solana_live_executor as _exec
from . import solana_sibot as _sol


def _fallback_guard_log(executor, input_mint, amount_raw, reason):
    try:
        _eff._guard_event(
            executor,
            action="SELL",
            input_mint=str(input_mint),
            output_mint=str(_sol.WSOL_MINT),
            amount_raw=int(amount_raw),
            trade_value_lamports=0,
            fee_cap_lamports=0,
            reason="ATOMIC_CLOSE_INELIGIBLE_FALLBACK: " + str(reason)[:700],
            details={"fallback": "managed_order_with_economic_caps"},
        )
    except Exception:
        pass


def sell_with_atomic_or_capped_legacy_fallback(self, input_mint: str, amount_raw: int):
    """Use atomic full close when provable; never trap a legacy position.

    Eligibility failures happen before any transaction is submitted.  Those cases
    fall back to the existing managed SELL, which still passes through the new
    dynamic fee/slippage/impact cap because the base SELL calls ``self.swap`` and
    ``self._order`` dynamically.  Once the atomic path itself starts, execution or
    simulation errors are not swallowed or retried through a different route.
    """
    actual = int(self.token_balance_raw(input_mint))
    if int(amount_raw) < actual:
        return _eff._PREV_SELL(self, input_mint, amount_raw)

    cfg = _eff._cfg(self.app)
    if not _eff._bool(cfg.get("live_require_atomic_full_close"), True):
        return _eff._PREV_SELL(self, input_mint, amount_raw)

    try:
        candidate = _eff._atomic_candidate(self, input_mint, amount_raw)
    except _exec.SolanaLiveError as exc:
        _fallback_guard_log(self, input_mint, amount_raw, exc)
        return _eff._PREV_SELL(self, input_mint, amount_raw)

    if candidate is None:
        return _eff._PREV_SELL(self, input_mint, amount_raw)
    return _eff.atomic_full_sell(self, input_mint, amount_raw, candidate)


def build_atomic_swap_excluding_rfq(executor, input_mint: str, amount_raw: int, slippage_bps: int) -> dict:
    """Keep the custom atomic route on the same deterministic non-RFQ universe."""
    params = {
        "inputMint": str(input_mint),
        "outputMint": str(_sol.WSOL_MINT),
        "amount": str(int(amount_raw)),
        "taker": str(executor.address),
        "slippageBps": str(int(slippage_bps)),
        "wrapAndUnwrapSol": "true",
        "nativeDestinationAccount": str(executor.address),
        "maxAccounts": "64",
        "excludeRouters": "jupiterz",
    }
    response = _eff.requests.get(
        _eff.JUPITER_BUILD_URL,
        params=params,
        headers=_eff._headers(executor),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error") or data.get("errorCode") or _eff._i(data.get("outAmount"), 0) <= 0 or not data.get("swapInstruction"):
        raise _exec.SolanaLiveError(
            "Jupiter /build failed: "
            + str(data.get("error") or data.get("errorMessage") or data.get("errorCode") or "invalid build")
        )
    return data


def install():
    # Replace the exported inner function before the final validation layer imports
    # it.  Updating the efficiency module symbol keeps runtime identity assertions
    # and later wrappers referring to the same canonical function.
    _eff.sell_with_atomic_account_close = sell_with_atomic_or_capped_legacy_fallback
    _eff._build_atomic_swap = build_atomic_swap_excluding_rfq
    _exec.SolanaLiveExecutor.sell = sell_with_atomic_or_capped_legacy_fallback
    print(
        "[solana-atomic-close-fallback] eligible_full_exit=atomic "
        "legacy_unproven_exit=capped_managed rfq_excluded=true"
    )


install()

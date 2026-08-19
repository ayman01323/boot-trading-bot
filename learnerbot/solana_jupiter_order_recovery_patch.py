from __future__ import annotations

import json

import requests

from . import solana_execution_efficiency_patch as _eff
from . import solana_live_executor as _exec
from . import solana_sibot as _sol


_PREV_ORDER = _eff.order_with_economic_caps


def _response_error_text(response) -> str:
    """Return Jupiter's useful error body without echoing the full request URL."""
    if response is None:
        return "no response body"
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        useful = {
            key: payload.get(key)
            for key in ("requestId", "error", "errorCode", "errorMessage", "message")
            if payload.get(key) not in (None, "")
        }
        if useful:
            return json.dumps(useful, separators=(",", ":"), default=str)[:900]
    try:
        text = str(response.text or "").strip()
    except Exception:
        text = ""
    return (text or "empty response body")[:900]


def _get_json(executor, params: dict, *, context: str) -> dict:
    response = requests.get(
        f"{_exec.JUPITER_BASE}/order",
        params=params,
        headers=_eff._headers(executor),
        timeout=30,
    )
    if not response.ok:
        raise _exec.SolanaLiveError(
            f"Jupiter {context} HTTP {response.status_code}: {_response_error_text(response)}"
        )
    try:
        data = response.json()
    except Exception as exc:
        raise _exec.SolanaLiveError(
            f"Jupiter {context} returned invalid JSON (HTTP {response.status_code})"
        ) from exc
    return dict(data or {})


def request_quote_with_error_body(executor, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict:
    params = {
        "inputMint": str(input_mint),
        "outputMint": str(output_mint),
        "amount": str(int(amount_raw)),
        "slippageBps": str(int(slippage_bps)),
        # Keep the audited single-wallet-signer route universe.  Do not silently
        # widen emergency exits to JupiterZ/RFQ, which can require another signer.
        "excludeRouters": "jupiterz",
    }
    data = _get_json(executor, params, context="quote")
    if data.get("errorCode") or _eff._i(data.get("outAmount") or data.get("outputAmount"), 0) <= 0:
        raise _exec.SolanaLiveError(
            "Jupiter quote failed: "
            + str(data.get("errorMessage") or data.get("error") or data.get("errorCode") or "no positive output")
        )
    return data


def order_with_http400_recovery(self, input_mint: str, output_mint: str, amount_raw: int) -> dict:
    """Build the same economically capped order, but recover safely from bad manual fee requests.

    Jupiter documents ``broadcastFeeType`` as relevant only when a priority fee or
    Jito tip is supplied.  The previous code sent ``broadcastFeeType=maxCap`` even
    when the dynamic fee budget left no priority/tip allowance.  This replacement
    omits that orphan parameter, preserves the configured slippage and RFQ exclusion,
    and retries one HTTP-400 order once without manual fee overrides.  The returned
    order still passes the existing fee/impact/rent validation before it can be signed.
    """
    if int(amount_raw) <= 0:
        raise _exec.SolanaLiveError("Swap amount must be positive")

    cfg = _eff._cfg(self.app)
    slippage_bps = max(1, min(10_000, _eff._i(cfg.get("live_order_slippage_bps"), 50)))

    quote = None
    if str(output_mint) == str(_sol.WSOL_MINT) and str(input_mint) != str(_sol.WSOL_MINT):
        quote = request_quote_with_error_body(self, input_mint, output_mint, amount_raw, slippage_bps)

    trade_value = _eff._trade_value_for_order(input_mint, output_mint, amount_raw, quote)
    if trade_value <= 0:
        raise _exec.SolanaLiveError("Cannot establish SOL trade value for economic fee cap")
    fee_cap = _eff.dynamic_fee_cap_lamports(cfg, trade_value)

    platform_hint = _eff._platform_fee_equivalent(quote or {}, trade_value)
    explicit_jito = 0
    if _eff._bool(cfg.get("live_enable_jito_tip"), False):
        explicit_jito = min(
            max(0, _eff._i(cfg.get("live_max_jito_tip_lamports"), 1000)),
            max(0, fee_cap - _eff.DEFAULT_BASE_FEE_LAMPORTS - platform_hint),
        )
        if explicit_jito < 1000:
            explicit_jito = 0

    priority_cap = max(
        0,
        fee_cap - _eff.DEFAULT_BASE_FEE_LAMPORTS - platform_hint - explicit_jito,
    )

    base_params = {
        "inputMint": str(input_mint),
        "outputMint": str(output_mint),
        "amount": str(int(amount_raw)),
        "taker": self.address,
        "excludeRouters": "jupiterz",
        "slippageBps": str(slippage_bps),
    }
    params = dict(base_params)
    if priority_cap > 0:
        params["priorityFeeLamports"] = str(int(priority_cap))
    if explicit_jito >= 1000:
        params["jitoTipLamports"] = str(int(explicit_jito))
    # Only send a fee-cap strategy when there is an actual manual fee/tip value.
    if "priorityFeeLamports" in params or "jitoTipLamports" in params:
        params["broadcastFeeType"] = "maxCap"

    try:
        order = _get_json(self, params, context="order")
        recovered = False
        first_error = ""
    except _exec.SolanaLiveError as exc:
        # Only retry the class of request this patch can safely simplify: a Jupiter
        # HTTP 400 from an order carrying manual fee controls.  No slippage widening,
        # RFQ enabling, amount change, or duplicate broadcast occurs here.
        if "HTTP 400:" not in str(exc) or params == base_params:
            raise
        first_error = str(exc)
        order = _get_json(self, base_params, context="order safe-retry")
        recovered = True

    order = _eff._validate_order(
        self,
        order,
        input_mint,
        output_mint,
        amount_raw,
        trade_value,
        fee_cap,
        cfg,
    )
    order["_requested_priority_fee_cap_lamports"] = int(priority_cap if not recovered else 0)
    order["_requested_jito_tip_lamports"] = int(explicit_jito if not recovered else 0)
    order["_jupiter_http400_safe_retry"] = bool(recovered)
    if recovered:
        order["_jupiter_first_order_error"] = first_error[:900]
    return order


def install():
    # Keep the invariant's canonical symbol and executor hook identical.
    _eff._request_quote_only = request_quote_with_error_body
    _eff.order_with_economic_caps = order_with_http400_recovery
    _exec.SolanaLiveExecutor._order = order_with_http400_recovery
    print(
        "[solana-jupiter-order-recovery] detailed_http_errors=true "
        "orphan_broadcast_fee_removed=true http400_fee_retry=true rfq_excluded=true"
    )


install()

from __future__ import annotations

"""Truthful SiBot 1 candidate reporting and exact-owned EXIT prefilter.

This patch is deliberately narrow:
- a pre-revalidation claim is never described as already LIVE;
- an EXIT candidate is not claimed unless the exact shadow lot has a matching
  OPEN LIVE bridge position for the same Telegram owner;
- no PoolCheck/RugCheck/liquidity/slippage/signer/risk threshold is changed;
- no fallback position is fabricated from mint/engine similarity.
"""

import html

from . import sibot1_solana_live_bridge_patch as _sol
from . import sibot1_trade_event_telegram_patch as _alerts

_PREV_PROCESS_CANDIDATE = _sol._process_candidate
_INSTALLED = False


def candidate_selected_truth(module, chain: str, app, tid, candidate) -> None:
    cid = _alerts._candidate_id(candidate)
    kind = str(candidate.get("kind") or "UNKNOWN").upper()
    engine = str(candidate.get("engine_id") or "unknown")
    verdict = str(candidate.get("poolcheck_verdict") or "UNSPECIFIED").upper()
    asset = _alerts._short_asset(_alerts._asset(candidate))

    if kind == "EXIT":
        next_gate = "owned LIVE position verified; exit safety checks pending"
    else:
        next_gate = "fresh LIVE PoolCheck/sellability/simulation revalidation pending"

    _alerts._send(
        module,
        app,
        tid,
        key=f"{chain}:{tid}:{cid}:{kind}:selected",
        ttl=3600,
        text=(
            "🎯 <b>SiBot 1 candidate selected for LIVE evaluation</b>\n"
            f"Chain: <b>{html.escape(chain.title())}</b>\n"
            f"Engine: <b>{html.escape(engine)}</b>\n"
            f"Action: <b>{html.escape(kind)}</b>\n"
            f"Asset: <code>{html.escape(asset)}</code>\n"
            f"Candidate PoolCheck: <b>{html.escape(verdict)}</b>\n"
            "LIVE status: <b>PENDING</b>\n"
            f"Next gate: <code>{html.escape(next_gate)}</code>"
        ),
    )


def process_candidate_with_owned_exit_prefilter(app, tid, candidate) -> None:
    kind = str(candidate.get("kind") or "").upper()
    if kind == "EXIT":
        lot_id = str(candidate.get("shadow_lot_id") or "").strip()
        if not lot_id:
            return
        # Exact ownership only.  Do not infer a LIVE position from mint/engine or
        # from a SHADOW lot; ambiguous reconciliation must remain explicit.
        if _sol._position(app, tid, lot_id) is None:
            return
    return _PREV_PROCESS_CANDIDATE(app, tid, candidate)


def install() -> None:
    global _INSTALLED
    if _INSTALLED or getattr(_sol, "_sibot1_candidate_truth_patch_installed", False):
        return

    # The trade-alert claim wrapper resolves this module-global function at call
    # time, so replacing it changes presentation only; claim/execution semantics
    # stay in the bridge.
    _alerts._candidate_selected = candidate_selected_truth
    _sol._process_candidate = process_candidate_with_owned_exit_prefilter
    _sol._sibot1_candidate_truth_patch_installed = True
    _INSTALLED = True
    print(
        "[sibot1-candidate-truth] installed=true prelive-label=PENDING "
        "unowned-exit-claim=false exact-lot-only=true safety-gates=unchanged",
        flush=True,
    )


install()

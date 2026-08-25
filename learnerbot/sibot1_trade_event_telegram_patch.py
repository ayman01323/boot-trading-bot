from __future__ import annotations

import hashlib
import html
import threading
import time

from . import sibot1_live_bridge_patch as _base
from . import sibot1_solana_live_bridge_patch as _sol

_LOCK = threading.RLock()
_SEEN: dict[str, float] = {}
_TLS = threading.local()
_INSTALLED = False


def _allow_event(key: str, ttl_seconds: int, *, now: float | None = None) -> bool:
    """Return True once per key/TTL; notification-only, never affects execution."""
    stamp = time.time() if now is None else float(now)
    with _LOCK:
        prior = float(_SEEN.get(str(key), 0.0) or 0.0)
        if prior and stamp - prior < max(1, int(ttl_seconds)):
            return False
        _SEEN[str(key)] = stamp
        if len(_SEEN) > 5000:
            cutoff = stamp - 86400
            for old_key, old_stamp in list(_SEEN.items())[:2500]:
                if old_stamp < cutoff:
                    _SEEN.pop(old_key, None)
        return True


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:20]


def _candidate_id(candidate) -> str:
    return str(
        candidate.get("candidate_id")
        or candidate.get("intent_id")
        or candidate.get("shadow_lot_id")
        or "unknown"
    )


def _asset(candidate) -> str:
    return str(candidate.get("asset_out") or candidate.get("asset") or candidate.get("token") or "")


def _short_asset(value: str) -> str:
    value = str(value or "")
    if len(value) <= 24:
        return value or "unknown"
    return value[:12] + "…" + value[-8:]


def _send(module, app, tid, *, key: str, text: str, ttl: int = 300) -> None:
    if not _allow_event(key, ttl):
        return
    try:
        module._notify(app, tid, text)
    except Exception:
        # Reporting must never become part of the trading success/failure path.
        pass


def _candidate_selected(module, chain: str, app, tid, candidate) -> None:
    cid = _candidate_id(candidate)
    kind = str(candidate.get("kind") or "UNKNOWN").upper()
    engine = str(candidate.get("engine_id") or "unknown")
    verdict = str(candidate.get("poolcheck_verdict") or "UNSPECIFIED").upper()
    asset = _short_asset(_asset(candidate))
    _send(
        module,
        app,
        tid,
        key=f"{chain}:{tid}:{cid}:{kind}:selected",
        ttl=3600,
        text=(
            "🎯 <b>SiBot 1 LIVE candidate selected</b>\n"
            f"Chain: <b>{html.escape(chain.title())}</b>\n"
            f"Engine: <b>{html.escape(engine)}</b>\n"
            f"Action: <b>{html.escape(kind)}</b>\n"
            f"Asset: <code>{html.escape(asset)}</code>\n"
            f"Candidate PoolCheck: <b>{html.escape(verdict)}</b>"
        ),
    )


def _wrap_claim(module, chain: str) -> None:
    original = module._claim
    if getattr(original, "_sibot1_trade_alert_wrapped", False):
        return

    def wrapped(app, tid, candidate):
        claimed, key = original(app, tid, candidate)
        if claimed:
            _candidate_selected(module, chain, app, tid, candidate)
        return claimed, key

    wrapped._sibot1_trade_alert_wrapped = True
    module._claim = wrapped


def _attempt_row(module, app, key):
    try:
        conn = module._db(app)
        try:
            row = conn.execute("SELECT * FROM attempts WHERE attempt_key=?", (str(key),)).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()
    except Exception:
        return {}


def _wrap_attempt_updates(module, chain: str) -> None:
    original = module._attempt_update
    if getattr(original, "_sibot1_trade_alert_wrapped", False):
        return

    def wrapped(app, key, status, *args, **kwargs):
        result = original(app, key, status, *args, **kwargs)
        state = str(status or "").upper()
        row = _attempt_row(module, app, key)
        tid = str(row.get("telegram_id") or "")
        if not tid:
            return result
        cid = str(row.get("candidate_id") or key)
        kind = str(row.get("kind") or "trade").upper()
        error = str(row.get("error") or "")
        tx = str(row.get("tx_hash") or row.get("tx_signature") or "")
        base_key = f"{chain}:{tid}:{cid}:{kind}"

        if chain == "base" and state == "BROADCAST":
            _send(
                module, app, tid,
                key=f"{base_key}:broadcast:{tx}", ttl=86400,
                text=(
                    "📡 <b>SiBot 1 Base transaction broadcast</b>\n"
                    f"Action: <b>{html.escape(kind)}</b>\n"
                    f"TX: <code>{html.escape(tx or 'pending')}</code>\n"
                    "Status: waiting for on-chain receipt"
                ),
            )
        elif state == "EXIT_DEFERRED":
            reason = error[:500] or "safe exit conditions are not currently proven"
            _send(
                module, app, tid,
                key=f"{base_key}:exit-deferred:{_fingerprint(reason)}", ttl=600,
                text=(
                    f"⏳ <b>SiBot 1 {html.escape(chain.title())} exit deferred</b>\n"
                    f"Reason: <code>{html.escape(reason)}</code>\n"
                    "Protection remains active; unsafe forced execution was not used."
                ),
            )
        elif chain == "solana" and state in {"REJECTED_OR_FAILED", "BLOCKED_INVALID_MINT"}:
            reason = error[:500] or state.replace("_", " ").title()
            _send(
                module, app, tid,
                key=f"{base_key}:{state}:{_fingerprint(reason)}", ttl=600,
                text=(
                    "❌ <b>SiBot 1 Solana execution stopped before confirmation</b>\n"
                    f"Action: <b>{html.escape(kind)}</b>\n"
                    f"Reason: <code>{html.escape(reason)}</code>"
                ),
            )
        elif chain == "solana" and state == "BLOCKED_MAX_POSITION":
            _send(
                module, app, tid,
                key=f"{base_key}:max-position", ttl=1800,
                text=(
                    "⏸ <b>SiBot 1 Solana entry skipped</b>\n"
                    "Reason: maximum LIVE-position limit already reached."
                ),
            )
        elif state in {"NO_LIVE_POSITION", "NO_WALLET_TOKEN"}:
            _send(
                module, app, tid,
                key=f"{base_key}:{state}", ttl=1800,
                text=(
                    f"ℹ️ <b>SiBot 1 {html.escape(chain.title())} exit skipped</b>\n"
                    f"Reason: <code>{html.escape(state.replace('_', ' ').title())}</code>"
                ),
            )
        return result

    wrapped._sibot1_trade_alert_wrapped = True
    module._attempt_update = wrapped


def _wrap_existing_notify(module, chain: str) -> None:
    """Deduplicate only known noisy alerts; never suppress confirmations/AUTO pauses."""
    original = module._notify
    if getattr(original, "_sibot1_trade_alert_wrapped", False):
        return

    def wrapped(app, tid, text):
        body = str(text or "")
        ttl = 0
        group = ""
        if "candidate blocked by LIVE PoolCheck" in body:
            ttl = 300
            group = "poolcheck-block"
        elif "bridge error" in body:
            ttl = 120
            group = "bridge-error"
        if ttl:
            key = f"{chain}:{tid}:{group}:{_fingerprint(body)}"
            if not _allow_event(key, ttl):
                return None
        return original(app, tid, text)

    wrapped._sibot1_trade_alert_wrapped = True
    module._notify = wrapped


def _wrap_solana_live_revalidation() -> None:
    original_revalidation = _sol._live_entry_revalidation
    if getattr(original_revalidation, "_sibot1_trade_alert_wrapped", False):
        return

    def wrapped_revalidation(app, mint, amount_sol):
        result = original_revalidation(app, mint, amount_sol)
        ctx = getattr(_TLS, "solana_entry", None)
        if ctx and bool(result[0]):
            tid, candidate = ctx
            evidence = dict(result[2] or {})
            cid = _candidate_id(candidate)
            _send(
                _sol, app, tid,
                key=f"solana:{tid}:{cid}:live-poolcheck-pass", ttl=3600,
                text=(
                    "🟢 <b>SiBot 1 Solana LIVE PoolCheck PASS</b>\n"
                    f"Engine: <b>{html.escape(str(candidate.get('engine_id') or 'unknown'))}</b>\n"
                    f"Reverse impact: <b>{html.escape(str(evidence.get('reverse_impact_bps') or '?'))} bps</b>\n"
                    f"3× stress impact: <b>{html.escape(str(evidence.get('stress_impact_bps') or '?'))} bps</b>\n"
                    f"Round-trip loss estimate: <b>{html.escape(str(evidence.get('roundtrip_loss_pct') or '?'))}%</b>"
                ),
            )
        return result

    wrapped_revalidation._sibot1_trade_alert_wrapped = True
    _sol._live_entry_revalidation = wrapped_revalidation

    original_execute = _sol._execute_entry
    if not getattr(original_execute, "_sibot1_trade_alert_wrapped", False):
        def wrapped_execute(app, tid, candidate, key):
            _TLS.solana_entry = (str(tid), candidate)
            try:
                return original_execute(app, tid, candidate, key)
            finally:
                try:
                    delattr(_TLS, "solana_entry")
                except AttributeError:
                    pass

        wrapped_execute._sibot1_trade_alert_wrapped = True
        _sol._execute_entry = wrapped_execute


def _wrap_solana_executor() -> None:
    original_cls = _sol.SolanaLiveExecutor
    if getattr(original_cls, "_sibot1_trade_alert_wrapped", False):
        return

    class NotifyingSolanaLiveExecutor(original_cls):
        _sibot1_trade_alert_wrapped = True

        def _simulate(self, signed_b64: str):
            ctx = getattr(_TLS, "solana_entry", None)
            try:
                result = super()._simulate(signed_b64)
            except Exception as exc:
                if ctx:
                    tid, candidate = ctx
                    cid = _candidate_id(candidate)
                    reason = f"{type(exc).__name__}: {exc}"[:500]
                    _send(
                        _sol, self.app, tid,
                        key=f"solana:{tid}:{cid}:simulation-fail:{_fingerprint(reason)}", ttl=600,
                        text=(
                            "❌ <b>SiBot 1 Solana signed simulation FAILED</b>\n"
                            f"Reason: <code>{html.escape(reason)}</code>\n"
                            "No successful execution confirmation was produced."
                        ),
                    )
                raise
            if ctx:
                tid, candidate = ctx
                cid = _candidate_id(candidate)
                _send(
                    _sol, self.app, tid,
                    key=f"solana:{tid}:{cid}:simulation-pass", ttl=3600,
                    text="🧪 <b>SiBot 1 Solana signed simulation PASS</b>\nExecution may proceed to Jupiter only while all LIVE gates remain valid.",
                )
            return result

        def buy(self, output_mint, amount_sol, reserve_sol):
            result = super().buy(output_mint, amount_sol, reserve_sol)
            self._notify_jupiter_success(result, "ENTRY")
            return result

        def sell(self, input_mint, amount_raw):
            result = super().sell(input_mint, amount_raw)
            self._notify_jupiter_success(result, "EXIT")
            return result

        def _notify_jupiter_success(self, result, action: str) -> None:
            signature = str((result or {}).get("signature") or "")
            if not signature:
                return
            ctx = getattr(_TLS, "solana_entry", None)
            if ctx:
                tid, candidate = ctx
                cid = _candidate_id(candidate)
            else:
                tid = self.telegram_id
                cid = _fingerprint(signature)
            _send(
                _sol, self.app, tid,
                key=f"solana:{tid}:{cid}:jupiter-success:{signature}", ttl=86400,
                text=(
                    "📡 <b>SiBot 1 Solana Jupiter execution returned Success</b>\n"
                    f"Action: <b>{html.escape(action)}</b>\n"
                    f"TX: <code>{html.escape(signature)}</code>\n"
                    "Final wallet-balance validation still follows before the trade is recorded as confirmed."
                ),
            )

    _sol.SolanaLiveExecutor = NotifyingSolanaLiveExecutor


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # First preserve existing alerts but throttle known repetitive categories.
    _wrap_existing_notify(_base, "base")
    _wrap_existing_notify(_sol, "solana")
    # Then add reporting hooks. These wrappers never alter return values or gates.
    _wrap_claim(_base, "base")
    _wrap_claim(_sol, "solana")
    _wrap_attempt_updates(_base, "base")
    _wrap_attempt_updates(_sol, "solana")
    _wrap_solana_live_revalidation()
    _wrap_solana_executor()
    _INSTALLED = True
    print("[sibot1-trade-alerts] installed=true reporting-only=true poolcheck-dedup=300s exit-dedup=600s")


install()

from __future__ import annotations

import os
import sys
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

import requests

DEFAULT_QUEUE_ROOT = Path(
    os.environ.get(
        "BOOT_REJECTED_OPPORTUNITY_DIR",
        "/home/ayman01323/BOOT/data/candidates/REJECTED OPPORTUNITY",
    )
)
DEFAULT_CODE_ROOT = Path(
    os.environ.get(
        "BOOT_REJECTED_OPPORTUNITY_CODE_ROOT",
        "/home/ayman01323/BOOT/datacentre/rejected_router",
    )
)

MARKET_TERMS = (
    "POOL", "LIQUID", "LP_", "LP ", "RUGCHECK", "REVERSE", "SELLABILITY",
    "PRICE IMPACT", "ROUNDTRIP", "ROUND-TRIP", "DEX", "HONEYPOT", "FREEZE",
    "MINT AUTHORITY", "DEVELOPER", "DEV_", "SIMULATION", "QUOTE", "SLIPPAGE",
    "ROUTE", "TOKEN_SECURITY", "COOLING", "WASH_VOLUME", "CROSS_POOL",
)
NON_MARKET_TERMS = (
    "ACCOUNT AUTOMATIC", "SIGNER", "NOT FUNDED", "INSUFFICIENT BALANCE",
    "INSUFFICIENT SOL", "PERMISSION IS OFF", "LIVE IS OFF", "AUTO IS OFF",
    "ARMED IS OFF", "NOT ARMED", "KILL-SWITCH", "CAPITAL BASIS", "DRAWDOWN",
)

_SOL_USD_CACHE: tuple[float, Decimal | None] = (0.0, None)
_SOL_USD_LOCK = threading.Lock()


def _enabled() -> bool:
    return str(os.environ.get("BOOT_REJECTED_OPPORTUNITY_ENABLED", "0")).strip().lower() in {
        "1", "true", "yes", "on", "y"
    }


def _telegram_enabled() -> bool:
    return str(os.environ.get("BOOT_REJECTED_TELEGRAM_ENABLED", "1")).strip().lower() in {
        "1", "true", "yes", "on", "y"
    }


def market_rejection(reason: str) -> bool:
    text = str(reason or "").upper()
    if not text or any(term in text for term in NON_MARKET_TERMS):
        return False
    return any(term in text for term in MARKET_TERMS)


def source_bot(default: str = "learnerbot") -> str:
    explicit = str(os.environ.get("BOOT_REJECTED_SOURCE_BOT", "")).strip().lower()
    if explicit:
        return explicit
    if str(os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "")).strip():
        return "claude"
    return str(default or "learnerbot").strip().lower()


def _queue():
    if str(DEFAULT_CODE_ROOT) not in sys.path:
        sys.path.insert(0, str(DEFAULT_CODE_ROOT))
    from boot_platform.rejected_opportunity_queue import RejectedOpportunityQueue
    return RejectedOpportunityQueue(DEFAULT_QUEUE_ROOT)


def _d(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out > 0 else None


def _pick(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _sol_usd_price(timeout: float = 5.0) -> Decimal | None:
    global _SOL_USD_CACHE
    now = time.monotonic()
    with _SOL_USD_LOCK:
        cached_at, cached = _SOL_USD_CACHE
        if cached is not None and now - cached_at < 60:
            return cached
    try:
        response = requests.get(
            "https://api.dexscreener.com/latest/dex/search",
            params={"q": "SOL/USDC"},
            timeout=timeout,
        )
        response.raise_for_status()
        pairs = (response.json() or {}).get("pairs") or []
        value = None
        for pair in pairs:
            if str(pair.get("chainId") or "").lower() != "solana":
                continue
            base = str((pair.get("baseToken") or {}).get("symbol") or "").upper()
            quote = str((pair.get("quoteToken") or {}).get("symbol") or "").upper()
            if base == "SOL" and quote in {"USDC", "USDT"}:
                value = _d(pair.get("priceUsd"))
                if value is not None:
                    break
    except Exception:
        value = None
    if value is not None:
        with _SOL_USD_LOCK:
            _SOL_USD_CACHE = (now, value)
    return value


def _dexscreener_liquidity_usd(
    *, chain: str, pool_address: str, token_address: str, timeout: float = 5.0
) -> Decimal | None:
    if str(chain or "").strip().lower() != "solana":
        return None
    try:
        if pool_address:
            response = requests.get(
                f"https://api.dexscreener.com/latest/dex/pairs/solana/{pool_address}",
                timeout=timeout,
            )
            response.raise_for_status()
            pairs = (response.json() or {}).get("pairs") or []
        elif token_address:
            response = requests.get(
                f"https://api.dexscreener.com/token-pairs/v1/solana/{token_address}",
                timeout=timeout,
            )
            response.raise_for_status()
            body = response.json() or []
            pairs = body if isinstance(body, list) else (body.get("pairs") or [])
        else:
            return None
    except Exception:
        return None
    values = [_d((pair.get("liquidity") or {}).get("usd")) for pair in pairs]
    usable = [value for value in values if value is not None]
    return max(usable) if usable else None


def _market_values(
    *, chain: str, pool_address: str, token_address: str, payload: Mapping[str, Any]
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    liquidity_usd = _d(_pick(payload, "liquidity_usd", "pool_liquidity_usd", "liquidityUsd"))
    if liquidity_usd is None:
        liquidity_usd = _dexscreener_liquidity_usd(
            chain=chain,
            pool_address=pool_address,
            token_address=token_address,
        )
    sol_usd = _d(_pick(payload, "sol_usd", "sol_price_usd", "solUsd"))
    if sol_usd is None and liquidity_usd is not None:
        sol_usd = _sol_usd_price()
    pool_sol_equivalent = liquidity_usd / sol_usd if liquidity_usd and sol_usd else None
    return liquidity_usd, sol_usd, pool_sol_equivalent


def _money(value: Decimal | None) -> str:
    return "unavailable" if value is None else f"${value:,.2f}"


def _sol(value: Decimal | None) -> str:
    return "unavailable" if value is None else f"{value:,.2f} SOL"


_DEXVIEW_CHAIN_SLUGS = {
    "solana": "solana",
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "bnb": "bsc",
    "bnbchain": "bsc",
    "base": "base",
    "arbitrum": "arbitrum",
    "arbitrum_one": "arbitrum",
    "polygon": "polygon",
}


def _dexview_url(chain: str, token_address: str) -> str:
    token = str(token_address or "").strip()
    slug = _DEXVIEW_CHAIN_SLUGS.get(str(chain or "").strip().lower())
    if not token or not slug:
        return ""
    return f"https://www.dexview.com/{slug}/{token}"


def format_rejected_telegram(
    *,
    candidate_id: str,
    chain: str,
    token_address: str,
    pool_address: str,
    dex: str,
    source: str,
    source_strategy_id: str,
    rejection_class: str,
    rejection_reason: str,
    payload: Mapping[str, Any],
    liquidity_usd: Decimal | None,
    sol_usd: Decimal | None,
    pool_sol_equivalent: Decimal | None,
) -> str:
    symbol = str(_pick(payload, "token_symbol", "symbol", "asset_symbol") or "").strip()
    price_usd = _d(_pick(payload, "price_usd", "price", "token_price_usd"))
    lines = [
        "⛔ REFUSED OPPORTUNITY",
        f"Chain: {str(chain or 'unknown').upper()}",
        f"Engine: {source or 'unknown'}",
        f"Strategy: {source_strategy_id or 'unknown'}",
        f"Token: {symbol or token_address or 'unknown'}",
    ]
    if symbol and token_address:
        lines.append(f"Mint/Address: {token_address}")
    if dex:
        lines.append(f"DEX: {dex}")
    if pool_address:
        lines.append(f"Pool: {pool_address}")
    dexview_url = _dexview_url(chain, token_address)
    if dexview_url:
        lines.append(f"DexView: {dexview_url}")
    lines.extend(
        [
            f"Pool value USD: {_money(liquidity_usd)}",
            f"Pool value SOL equivalent: {_sol(pool_sol_equivalent)}",
            f"SOL/USD: {_money(sol_usd)}",
            f"Token price: {_money(price_usd)}",
            f"Refusal class: {rejection_class or 'unknown'}",
            f"Reason: {rejection_reason or 'unknown'}",
            f"Candidate: {candidate_id or 'unknown'}",
        ]
    )
    return "\n".join(lines)


def _telegram_worker(
    *,
    candidate_id: str,
    chain: str,
    token_address: str,
    pool_address: str,
    dex: str,
    source: str,
    source_strategy_id: str,
    rejection_class: str,
    rejection_reason: str,
    payload: Mapping[str, Any],
) -> None:
    try:
        from learnerbot.config import AppSettings
        from learnerbot.telegram import send_to_chats

        cfg = AppSettings.load()
        if not cfg.telegram_bot_token or not cfg.telegram_chat_ids:
            return
        liquidity_usd, sol_usd, pool_sol_equivalent = _market_values(
            chain=chain,
            pool_address=pool_address,
            token_address=token_address,
            payload=payload,
        )
        send_to_chats(
            cfg.telegram_bot_token,
            cfg.telegram_chat_ids,
            format_rejected_telegram(
                candidate_id=candidate_id,
                chain=chain,
                token_address=token_address,
                pool_address=pool_address,
                dex=dex,
                source=source,
                source_strategy_id=source_strategy_id,
                rejection_class=rejection_class,
                rejection_reason=rejection_reason,
                payload=payload,
                liquidity_usd=liquidity_usd,
                sol_usd=sol_usd,
                pool_sol_equivalent=pool_sol_equivalent,
            ),
        )
    except Exception as exc:
        print(
            "[rejected-opportunity-telegram] sent=false error=%s" % type(exc).__name__,
            file=sys.stderr,
            flush=True,
        )


def notify_rejection_only(
    *,
    candidate_id: str,
    chain: str,
    token_address: str,
    rejection_class: str,
    rejection_reason: str,
    source: str = "learnerbot",
    pool_address: str = "",
    dex: str = "",
    source_strategy_id: str = "",
    source_event_id: str = "",
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Send one refusal alert without inserting it into the SiRisky queue."""
    if not _telegram_enabled():
        return
    # Deployment smoke tests must never generate operator-facing Telegram alerts.
    if str(source_event_id or "") == "writer-smoke" or str(token_address or "") == "WriterSmokeMint":
        return
    resolved_payload = dict(payload or {})
    resolved_pool = str(
        pool_address
        or _pick(resolved_payload, "pool_id", "pool_address", "pair_address")
        or ""
    ).strip()
    resolved_dex = str(dex or _pick(resolved_payload, "dex", "venue") or "").strip()
    thread = threading.Thread(
        target=_telegram_worker,
        kwargs={
            "candidate_id": str(candidate_id or ""),
            "chain": str(chain or "").strip().lower(),
            "token_address": str(token_address or "").strip(),
            "pool_address": resolved_pool,
            "dex": resolved_dex,
            "source": source_bot(source),
            "source_strategy_id": str(source_strategy_id or ""),
            "rejection_class": str(rejection_class or "MARKET_RISK_REJECT"),
            "rejection_reason": str(rejection_reason or ""),
            "payload": resolved_payload,
        },
        name="rejected-opportunity-telegram",
        daemon=True,
    )
    thread.start()


def publish_rejection(
    *,
    chain: str,
    token_address: str,
    rejection_class: str,
    rejection_reason: str,
    source: str = "learnerbot",
    pool_address: str = "",
    dex: str = "",
    source_strategy_id: str = "",
    source_event_id: str = "",
    priority: int = 60,
    payload: dict[str, Any] | None = None,
    observed_at: int | None = None,
    require_market_reason: bool = True,
):
    """Publish one rejection without ever blocking the trading process.

    Queue/file and Telegram failures are swallowed. Refusal reporting is an
    observability side effect only and never changes a trading decision.
    """
    if not _enabled():
        return None
    token = str(token_address or "").strip()
    reason = str(rejection_reason or "").strip()
    if not token or (require_market_reason and not market_rejection(reason)):
        return None
    try:
        resolved_source = source_bot(source)
        resolved_payload = dict(payload or {})
        resolved_pool = str(
            pool_address
            or _pick(resolved_payload, "pool_id", "pool_address", "pair_address")
            or ""
        ).strip()
        resolved_dex = str(dex or _pick(resolved_payload, "dex", "venue") or "").strip()
        result = _queue().publish(
            chain=str(chain or "").strip().lower(),
            token_address=token,
            source_bot=resolved_source,
            rejection_reason=reason,
            pool_address=resolved_pool,
            dex=resolved_dex,
            source_strategy_id=str(source_strategy_id or ""),
            source_event_id=str(source_event_id or ""),
            rejection_class=str(rejection_class or "MARKET_RISK_REJECT"),
            priority=int(priority),
            observed_at=int(observed_at or time.time()),
            payload=resolved_payload,
        )
        print(
            "[rejected-opportunity] published=true source=%s chain=%s class=%s candidate=%s status=%s" % (
                resolved_source, str(chain or "").lower(), str(rejection_class or ""),
                result.candidate_id, result.status,
            ),
            flush=True,
        )
        if bool(result.inserted_observation):
            notify_rejection_only(
                candidate_id=result.candidate_id,
                chain=str(chain or "").strip().lower(),
                token_address=token,
                pool_address=resolved_pool,
                dex=resolved_dex,
                source=resolved_source,
                source_strategy_id=str(source_strategy_id or ""),
                source_event_id=str(source_event_id or ""),
                rejection_class=str(rejection_class or "MARKET_RISK_REJECT"),
                rejection_reason=reason,
                payload=resolved_payload,
            )
        return result
    except Exception as exc:  # data publication must never break trading safety logic
        print(
            "[rejected-opportunity] published=false error=%s source=%s" % (
                type(exc).__name__, source_bot(source)
            ),
            file=sys.stderr,
            flush=True,
        )
        return None

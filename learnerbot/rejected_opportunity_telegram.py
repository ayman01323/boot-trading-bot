from __future__ import annotations

import os
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import requests

_SOL_USD_CACHE: tuple[float, Decimal | None] = (0.0, None)
_CACHE_LOCK = threading.Lock()


def _enabled() -> bool:
    return str(os.environ.get("BOOT_REJECTED_TELEGRAM_ENABLED", "1")).strip().lower() in {
        "1", "true", "yes", "on", "y"
    }


def _d(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out > 0 else None


def _pick(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def _sol_usd_price(timeout: float = 5.0) -> Decimal | None:
    global _SOL_USD_CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
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
        prices: list[Decimal] = []
        for pair in pairs:
            if str(pair.get("chainId") or "").lower() != "solana":
                continue
            base = str((pair.get("baseToken") or {}).get("symbol") or "").upper()
            quote = str((pair.get("quoteToken") or {}).get("symbol") or "").upper()
            if base != "SOL" or quote not in {"USDC", "USDT"}:
                continue
            price = _d(pair.get("priceUsd"))
            if price is not None:
                prices.append(price)
        value = prices[0] if prices else None
    except Exception:
        value = None
    if value is not None:
        with _CACHE_LOCK:
            _SOL_USD_CACHE = (now, value)
    return value


def _dexscreener_liquidity_usd(
    *,
    chain: str,
    pool_address: str,
    token_address: str,
    timeout: float = 5.0,
) -> Decimal | None:
    if str(chain or "").strip().lower() != "solana":
        return None
    try:
        if pool_address:
            url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{pool_address}"
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            pairs = (response.json() or {}).get("pairs") or []
        elif token_address:
            url = f"https://api.dexscreener.com/token-pairs/v1/solana/{token_address}"
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            body = response.json() or []
            pairs = body if isinstance(body, list) else (body.get("pairs") or [])
        else:
            return None
    except Exception:
        return None

    best: Decimal | None = None
    for pair in pairs:
        value = _d((pair.get("liquidity") or {}).get("usd"))
        if value is not None and (best is None or value > best):
            best = value
    return best


def _market_values(
    *,
    chain: str,
    pool_address: str,
    token_address: str,
    payload: Mapping[str, Any],
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


def format_rejected_opportunity(
    *,
    candidate_id: str,
    chain: str,
    token_address: str,
    pool_address: str,
    dex: str,
    source_bot: str,
    source_strategy_id: str,
    rejection_class: str,
    rejection_reason: str,
    payload: Mapping[str, Any],
    liquidity_usd: Decimal | None,
    sol_usd: Decimal | None,
    pool_sol_equivalent: Decimal | None,
) -> str:
    token_symbol = str(_pick(payload, "token_symbol", "symbol", "asset_symbol") or "").strip()
    token_label = token_symbol or token_address or "unknown"
    price_usd = _d(_pick(payload, "price_usd", "price", "token_price_usd"))
    lines = [
        "⛔ REFUSED OPPORTUNITY",
        f"Chain: {str(chain or 'unknown').upper()}",
        f"Engine: {source_bot or 'unknown'}",
        f"Strategy: {source_strategy_id or 'unknown'}",
        f"Token: {token_label}",
    ]
    if token_symbol and token_address:
        lines.append(f"Mint/Address: {token_address}")
    if dex:
        lines.append(f"DEX: {dex}")
    if pool_address:
        lines.append(f"Pool: {pool_address}")
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


def _notify_worker(
    *,
    candidate_id: str,
    chain: str,
    token_address: str,
    pool_address: str,
    dex: str,
    source_bot: str,
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
        text = format_rejected_opportunity(
            candidate_id=candidate_id,
            chain=chain,
            token_address=token_address,
            pool_address=pool_address,
            dex=dex,
            source_bot=source_bot,
            source_strategy_id=source_strategy_id,
            rejection_class=rejection_class,
            rejection_reason=rejection_reason,
            payload=payload,
            liquidity_usd=liquidity_usd,
            sol_usd=sol_usd,
            pool_sol_equivalent=pool_sol_equivalent,
        )
        send_to_chats(cfg.telegram_bot_token, cfg.telegram_chat_ids, text)
    except Exception as exc:
        print(
            f"[rejected-opportunity-telegram] sent=false error={type(exc).__name__}",
            flush=True,
        )


def notify_rejected_opportunity_async(
    *,
    candidate_id: str,
    inserted_observation: bool,
    chain: str,
    token_address: str,
    pool_address: str,
    dex: str,
    source_bot: str,
    source_strategy_id: str,
    rejection_class: str,
    rejection_reason: str,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Best-effort Telegram side effect; never delays or changes a trade decision."""
    if not _enabled() or not inserted_observation:
        return
    thread = threading.Thread(
        target=_notify_worker,
        kwargs={
            "candidate_id": str(candidate_id or ""),
            "chain": str(chain or ""),
            "token_address": str(token_address or ""),
            "pool_address": str(pool_address or ""),
            "dex": str(dex or ""),
            "source_bot": str(source_bot or ""),
            "source_strategy_id": str(source_strategy_id or ""),
            "rejection_class": str(rejection_class or ""),
            "rejection_reason": str(rejection_reason or ""),
            "payload": dict(payload or {}),
        },
        name="rejected-opportunity-telegram",
        daemon=True,
    )
    thread.start()

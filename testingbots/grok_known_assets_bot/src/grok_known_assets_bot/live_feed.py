from __future__ import annotations

import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .core import Asset, Journal, RiskConfig
from .feed_safety import FeedSafetyError, JupiterRouteEvidence, ProviderObservation, SafeSnapshotBuilder, ValidatedSnapshotEnvelope

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class FeedWarmupError(FeedSafetyError):
    pass


@dataclass(frozen=True)
class LiveFeedSettings:
    poll_seconds: float = 15.0
    request_timeout_seconds: float = 12.0
    quote_size_sol: float = 0.05
    reverse_quote_usdc: float = 10.0
    slippage_bps: int = 50
    assumed_fee_bps: float = 5.0
    history_window_seconds: int = 1_200

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "LiveFeedSettings":
        cfg = raw.get("paper_feed", {}) if isinstance(raw, dict) else {}
        return cls(
            poll_seconds=max(5.0, float(cfg.get("poll_seconds", 15.0))),
            request_timeout_seconds=max(3.0, float(cfg.get("request_timeout_seconds", 12.0))),
            quote_size_sol=max(0.001, float(cfg.get("quote_size_sol", 0.05))),
            reverse_quote_usdc=max(1.0, float(cfg.get("reverse_quote_usdc", 10.0))),
            slippage_bps=max(1, min(500, int(cfg.get("slippage_bps", 50)))),
            assumed_fee_bps=max(0.0, float(cfg.get("assumed_fee_bps", 5.0))),
            history_window_seconds=max(900, int(cfg.get("history_window_seconds", 1_200))),
        )


def _get_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "grok-known-assets-paper/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _quote(input_mint: str, output_mint: str, amount: int, *, slippage_bps: int, timeout: float) -> dict[str, Any]:
    params = urllib.parse.urlencode({"inputMint": input_mint, "outputMint": output_mint, "amount": str(amount), "slippageBps": str(slippage_bps)})
    data = _get_json(f"https://lite-api.jup.ag/swap/v1/quote?{params}", timeout)
    if not isinstance(data, dict) or not data.get("outAmount"):
        raise FeedSafetyError("JUPITER_QUOTE_UNAVAILABLE")
    return data


def _route_pool_ids(*quotes: dict[str, Any]) -> tuple[str, ...]:
    pools: list[str] = []
    for quote in quotes:
        for leg in quote.get("routePlan", []) or []:
            swap = leg.get("swapInfo", {}) if isinstance(leg, dict) else {}
            amm = str(swap.get("ammKey") or "").strip()
            if amm and amm not in pools:
                pools.append(amm)
    return tuple(pools)


def _price_impact_bps(*quotes: dict[str, Any]) -> float:
    impacts: list[float] = []
    for quote in quotes:
        try:
            impacts.append(abs(float(quote.get("priceImpactPct", 0.0))) * 10_000.0)
        except (TypeError, ValueError):
            continue
    return max(impacts, default=0.0)


def _select_solana_pair(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, list):
        raise FeedSafetyError("DEXSCREENER_INVALID_RESPONSE")
    candidates: list[dict[str, Any]] = []
    for pair in payload:
        if not isinstance(pair, dict) or str(pair.get("chainId", "")).lower() != "solana":
            continue
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        addresses = {str(base.get("address", "")), str(quote.get("address", ""))}
        if SOL_MINT not in addresses:
            continue
        symbols = {str(base.get("symbol", "")).upper(), str(quote.get("symbol", "")).upper()}
        if "USDC" not in symbols and "USDT" not in symbols:
            continue
        try:
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0.0)
            price = float(pair.get("priceUsd") or 0.0)
        except (TypeError, ValueError):
            continue
        if liquidity > 0 and price > 0:
            candidates.append(pair)
    if not candidates:
        raise FeedSafetyError("DEXSCREENER_NO_SOL_STABLE_PAIR")
    return max(candidates, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0))


class SolanaNativeLiveFeed:
    """Public-market collector for native SOL PAPER trading only; no wallet/signing/broadcast path."""

    def __init__(self, assets: dict[str, Asset], risk: RiskConfig, journal: Journal, raw_config: dict[str, Any]) -> None:
        self.assets = assets
        self.risk = risk
        self.journal = journal
        self.settings = LiveFeedSettings.from_raw(raw_config)
        self.builder = SafeSnapshotBuilder(assets, risk)

    @staticmethod
    def supported(asset: Asset) -> bool:
        return asset.enabled and asset.chain == "solana" and asset.is_native and asset.symbol == "SOL"

    def _load_history(self, asset_key: str) -> list[tuple[float, float]]:
        raw = self.journal.get_state(f"paper_feed_history:{asset_key}", [])
        rows: list[tuple[float, float]] = []
        if isinstance(raw, list):
            for item in raw:
                try:
                    ts, price = float(item[0]), float(item[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if ts > 0 and price > 0:
                    rows.append((ts, price))
        return rows

    def _append_history(self, asset_key: str, ts: float, price: float) -> list[tuple[float, float]]:
        cutoff = ts - self.settings.history_window_seconds
        rows = [(t, p) for t, p in self._load_history(asset_key) if t >= cutoff]
        if not rows or ts - rows[-1][0] >= max(1.0, self.settings.poll_seconds * 0.50):
            rows.append((ts, price))
        self.journal.set_state(f"paper_feed_history:{asset_key}", [[round(t, 3), p] for t, p in rows[-500:]])
        return rows

    @staticmethod
    def _price_at_or_before(rows: list[tuple[float, float]], target_ts: float) -> float | None:
        eligible = [p for t, p in rows if t <= target_ts]
        return eligible[-1] if eligible else None

    def _metrics(self, rows: list[tuple[float, float]], now: float, current: float) -> tuple[float, float, float, float]:
        refs: dict[int, float] = {}
        for seconds in (60, 300, 900):
            p = self._price_at_or_before(rows, now - seconds)
            if p is None or p <= 0:
                raise FeedWarmupError(f"PRICE_HISTORY_WARMUP:{seconds}s")
            refs[seconds] = p
        ret = lambda old: (current / old - 1.0) * 100.0
        five_minute = [(t, p) for t, p in rows if t >= now - 300]
        step_returns = [(b / a - 1.0) * 100.0 for (_, a), (_, b) in zip(five_minute, five_minute[1:]) if a > 0 and b > 0]
        vol_5m = statistics.pstdev(step_returns) if len(step_returns) >= 2 else 0.0
        if not math.isfinite(vol_5m):
            vol_5m = 0.0
        return ret(refs[60]), ret(refs[300]), ret(refs[900]), vol_5m

    def collect(self, asset: Asset, *, now: float | None = None) -> ValidatedSnapshotEnvelope:
        if not self.supported(asset):
            raise FeedSafetyError(f"UNSUPPORTED_REAL_FEED_ASSET:{asset.key}")
        now = float(time.time() if now is None else now)
        now_ms = int(now * 1000)
        dex = _get_json(f"https://api.dexscreener.com/token-pairs/v1/solana/{SOL_MINT}", self.settings.request_timeout_seconds)
        pair = _select_solana_pair(dex)
        dex_price = float(pair["priceUsd"])
        liquidity = float((pair.get("liquidity") or {}).get("usd") or 0.0)
        volume_5m = float((pair.get("volume") or {}).get("m5") or 0.0)
        history = self._append_history(asset.key, now, dex_price)
        ret_1m, ret_5m, ret_15m, vol_5m = self._metrics(history, now, dex_price)

        sell_amount_lamports = int(self.settings.quote_size_sol * 1_000_000_000)
        buy_amount_micro_usdc = int(self.settings.reverse_quote_usdc * 1_000_000)
        sell = _quote(SOL_MINT, USDC_MINT, sell_amount_lamports, slippage_bps=self.settings.slippage_bps, timeout=self.settings.request_timeout_seconds)
        buy = _quote(USDC_MINT, SOL_MINT, buy_amount_micro_usdc, slippage_bps=self.settings.slippage_bps, timeout=self.settings.request_timeout_seconds)
        sol_sold = sell_amount_lamports / 1_000_000_000.0
        usdc_out = int(sell["outAmount"]) / 1_000_000.0
        sol_out = int(buy["outAmount"]) / 1_000_000_000.0
        if sol_sold <= 0 or usdc_out <= 0 or sol_out <= 0:
            raise FeedSafetyError("JUPITER_INVALID_QUOTE_AMOUNT")
        bid = usdc_out / sol_sold
        ask = self.settings.reverse_quote_usdc / sol_out
        if ask < bid:
            mid = (ask + bid) / 2.0
            bid = mid
            ask = mid

        observation = ProviderObservation(provider="dexscreener", chain=asset.chain, address=asset.address, source_timestamp_ms=now_ms, received_at_ms=now_ms, price_usd=dex_price, liquidity_usd=liquidity, volume_5m_usd=volume_5m, ret_1m_pct=ret_1m, ret_5m_pct=ret_5m, ret_15m_pct=ret_15m, vol_5m_pct=vol_5m, pool_id=str(pair.get("pairAddress") or ""))
        route = JupiterRouteEvidence(chain=asset.chain, address=asset.address, checked_at_ms=now_ms, forward_ok=True, reverse_ok=True, bid=bid, ask=ask, reverse_bid=bid, impact_bps=_price_impact_bps(sell, buy), fees_bps=self.settings.assumed_fee_bps, slippage_bps=float(self.settings.slippage_bps), route_id="|".join(_route_pool_ids(sell, buy)), asset_pool_ids=_route_pool_ids(sell, buy))
        return self.builder.build(chain=asset.chain, address=asset.address, observations=(observation,), jupiter=route, pool_safety=None, now_ms=now_ms)

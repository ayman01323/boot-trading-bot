from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from decimal import Decimal

import requests

from .csvio import as_bool, read_rows, write_rows_atomic
from .jupiter import quote_only, WSOL_MINT, USDC_MINT
from .models import MarketSnapshot
from .wallet import WalletStore

GECKO_NEW_POOLS = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
DEXSCREENER_PAIR = "https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}"

CANDIDATE_HEADERS = [
    "discovered_epoch",
    "pool_id",
    "chain",
    "dex",
    "pair_address",
    "base_mint",
    "quote_mint",
    "age_seconds",
    "age_class",
    "temperature",
    "liquidity_usd",
    "volume_m5_usd",
    "buys_m5",
    "sells_m5",
    "score",
    "probe_sol",
    "source",
    "status",
]


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _int(value, default=0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _tail(value: str) -> str:
    value = str(value or "").strip()
    return value[7:] if value.startswith("solana_") else value


def _rel_id(item: dict, name: str) -> str:
    try:
        return _tail(item["relationships"][name]["data"]["id"])
    except Exception:
        return ""


def _epoch(value) -> int:
    s = str(value or "").strip()
    if not s:
        return 0
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


class Stage1Data:
    def __init__(self, settings):
        self.settings = settings

    def _rt_bool(self, key: str, default=False) -> bool:
        return as_bool(self.settings.runtime().get(key), default)

    def _rt_int(self, key: str, default: int) -> int:
        return _int(self.settings.runtime().get(key), default)

    def _rt_float(self, key: str, default: float) -> float:
        return _num(self.settings.runtime().get(key), default)

    @property
    def candidate_path(self):
        return self.settings.csv_dir / "stage1_candidates.csv"

    @property
    def discovery_state_path(self):
        return self.settings.data_dir / "stage1_discovery_state.json"

    def _existing_candidate_count(self) -> int:
        try:
            return len(read_rows(self.candidate_path))
        except Exception:
            return 0

    def _last_discovery_epoch(self) -> int:
        try:
            data = json.loads(self.discovery_state_path.read_text(encoding="utf-8"))
            return int(data.get("last_epoch") or 0)
        except Exception:
            return 0

    def _save_state(self, **values):
        data = {"last_epoch": int(time.time()), **values}
        tmp = self.discovery_state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        tmp.replace(self.discovery_state_path)

    def _dex_allowed(self, dex: str) -> bool:
        raw = str(self.settings.runtime().get("auto_dex_allowlist") or "raydium,pump,meteora,orca,moonit")
        allowed = [x.strip().lower() for x in raw.split(",") if x.strip()]
        if not allowed:
            return True
        d = str(dex or "").lower()
        return any(x in d for x in allowed)

    def _enrich_dexscreener(self, pair_address: str) -> dict:
        try:
            r = requests.get(
                DEXSCREENER_PAIR.format(pair_address=pair_address),
                headers={"User-Agent": "SiRisky/0.2-auto-discovery"},
                timeout=12,
            )
            r.raise_for_status()
            pairs = (r.json() or {}).get("pairs") or []
            p = pairs[0] if pairs else {}
            tx5 = (p.get("txns") or {}).get("m5") or {}
            return {
                "liquidity_usd": _num((p.get("liquidity") or {}).get("usd"), 0),
                "volume_m5_usd": _num((p.get("volume") or {}).get("m5"), 0),
                "buys_m5": _int(tx5.get("buys"), 0),
                "sells_m5": _int(tx5.get("sells"), 0),
                "pair_created_epoch": _int(p.get("pairCreatedAt"), 0) // 1000,
                "dex": str(p.get("dexId") or "").strip(),
            }
        except Exception:
            return {}

    @staticmethod
    def _temperature(volume_m5: float, buys_m5: int, sells_m5: int) -> str:
        tx = buys_m5 + sells_m5
        if tx >= 100 or volume_m5 >= 10_000:
            return "HOT"
        if tx >= 20 or volume_m5 >= 2_000:
            return "WARM"
        return "COLD"

    @staticmethod
    def _age_class(age_seconds: int) -> str:
        if age_seconds <= 15 * 60:
            return "NEW"
        if age_seconds <= 2 * 60 * 60:
            return "EARLY"
        return "ESTABLISHED"

    @staticmethod
    def _score(liquidity_usd: float, volume_m5: float, buys_m5: int, sells_m5: int, age_seconds: int) -> float:
        activity = buys_m5 + sells_m5
        score = (
            12.0 * math.log10(max(liquidity_usd, 1.0))
            + min(activity, 200) * 0.18
            + min(volume_m5, 50_000) / 1_500.0
            - min(age_seconds / 900.0, 12.0)
        )
        return round(max(0.0, min(100.0, score)), 3)

    def discover(self, force=False) -> dict:
        rt = self.settings.runtime()
        if not force and not as_bool(rt.get("auto_discovery_enabled"), False):
            return {"status": "DISABLED", "count": self._existing_candidate_count(), "updated": False}

        now = int(time.time())
        interval = max(30, _int(rt.get("auto_discovery_interval_seconds"), 60))
        if not force and now - self._last_discovery_epoch() < interval:
            return {"status": "CACHED", "count": self._existing_candidate_count(), "updated": False}

        limit = max(1, min(100, _int(rt.get("auto_candidate_limit"), 25)))
        enrich_limit = max(0, min(limit, _int(rt.get("auto_enrich_limit"), 12)))
        min_liquidity = max(0.0, _num(rt.get("auto_min_liquidity_usd"), 250.0))
        max_age = max(60, _int(rt.get("auto_max_age_seconds"), 7200))
        probe_sol = max(0.000001, _num(rt.get("auto_probe_sol"), 0.0005))

        try:
            response = requests.get(
                GECKO_NEW_POOLS,
                params={"include": "base_token,quote_token,dex", "page": "1"},
                headers={
                    "Accept": "application/json;version=20230203",
                    "User-Agent": "SiRisky/0.2-auto-discovery",
                },
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json() or {}
            raw_pools = payload.get("data") or []
        except Exception as exc:
            self._save_state(status="ERROR", error=type(exc).__name__, count=self._existing_candidate_count())
            return {"status": "ERROR", "error": type(exc).__name__, "count": self._existing_candidate_count(), "updated": False}

        candidates = []
        enrich_used = 0
        for item in raw_pools:
            attrs = item.get("attributes") or {}
            pair_address = str(attrs.get("address") or _tail(item.get("id"))).strip()
            base_mint = _rel_id(item, "base_token")
            quote_mint = _rel_id(item, "quote_token")
            dex = _rel_id(item, "dex") or str(attrs.get("dex_id") or "").strip()

            if base_mint in {WSOL_MINT, USDC_MINT} and quote_mint not in {WSOL_MINT, USDC_MINT}:
                base_mint, quote_mint = quote_mint, base_mint
            if not pair_address or not base_mint or base_mint in {WSOL_MINT, USDC_MINT}:
                continue
            if dex and not self._dex_allowed(dex):
                continue

            created_epoch = _epoch(attrs.get("pool_created_at"))
            age_seconds = max(0, now - created_epoch) if created_epoch else max_age
            liquidity_usd = _num(attrs.get("reserve_in_usd"), 0)
            volume_m5 = _num((attrs.get("volume_usd") or {}).get("m5"), 0)
            tx5 = (attrs.get("transactions") or {}).get("m5") or {}
            buys_m5 = _int(tx5.get("buys"), 0)
            sells_m5 = _int(tx5.get("sells"), 0)

            if enrich_used < enrich_limit:
                enriched = self._enrich_dexscreener(pair_address)
                enrich_used += 1
                if enriched:
                    liquidity_usd = enriched.get("liquidity_usd") or liquidity_usd
                    volume_m5 = enriched.get("volume_m5_usd") or volume_m5
                    buys_m5 = enriched.get("buys_m5") or buys_m5
                    sells_m5 = enriched.get("sells_m5") or sells_m5
                    dex = enriched.get("dex") or dex
                    if enriched.get("pair_created_epoch"):
                        age_seconds = max(0, now - int(enriched["pair_created_epoch"]))

            if age_seconds > max_age or liquidity_usd < min_liquidity:
                continue
            if dex and not self._dex_allowed(dex):
                continue

            temperature = self._temperature(volume_m5, buys_m5, sells_m5)
            candidates.append(
                {
                    "discovered_epoch": now,
                    "pool_id": pair_address,
                    "chain": "solana",
                    "dex": dex or "unknown",
                    "pair_address": pair_address,
                    "base_mint": base_mint,
                    "quote_mint": quote_mint,
                    "age_seconds": age_seconds,
                    "age_class": self._age_class(age_seconds),
                    "temperature": temperature,
                    "liquidity_usd": f"{liquidity_usd:.6f}",
                    "volume_m5_usd": f"{volume_m5:.6f}",
                    "buys_m5": buys_m5,
                    "sells_m5": sells_m5,
                    "score": f"{self._score(liquidity_usd, volume_m5, buys_m5, sells_m5, age_seconds):.3f}",
                    "probe_sol": f"{probe_sol:.9f}",
                    "source": "geckoterminal+dexscreener",
                    "status": "DISCOVERED",
                }
            )

        candidates.sort(key=lambda r: (-_num(r.get("score"), 0), _int(r.get("age_seconds"), max_age)))
        candidates = candidates[:limit]
        write_rows_atomic(self.candidate_path, CANDIDATE_HEADERS, candidates)
        self._save_state(status="OK", count=len(candidates), source_count=len(raw_pools))
        return {"status": "OK", "count": len(candidates), "source_count": len(raw_pools), "updated": True}

    def discover_if_due(self) -> dict:
        return self.discover(force=False)

    def snapshot(self, pool: dict, probe_sol: float) -> MarketSnapshot:
        mint = str(pool.get("base_mint") or "").strip()
        if not mint:
            raise ValueError("selected pool has no base_mint")
        wallet = WalletStore(self.settings)
        taker = wallet.address()
        lamports = max(1, int(Decimal(str(probe_sol)) * Decimal(1_000_000_000)))
        buy = quote_only(self.settings, taker, WSOL_MINT, mint, lamports)
        out = int(buy["out_amount"] or 0)
        if out <= 0:
            raise RuntimeError("Jupiter buy quote returned zero output")
        sell = quote_only(self.settings, taker, mint, WSOL_MINT, out)
        back = int(sell["out_amount"] or 0)
        round_trip = max(0.0, (lamports - back) / lamports * 100.0)
        exit_health = max(0.0, min(100.0, (back / lamports) * 100.0))
        return MarketSnapshot(
            pool_id=str(pool.get("pool_id") or mint[:8]),
            mint=mint,
            age_class=str(pool.get("age_class") or "NEW").upper(),
            temperature=str(pool.get("temperature_hint") or pool.get("temperature") or "COLD").upper(),
            buy_in_raw=lamports,
            expected_token_raw=out,
            round_trip_cost_pct=round_trip,
            exit_health_pct=exit_health,
            timestamp=int(time.time()),
            meta={"buy_router": buy["router"], "sell_router": sell["router"]},
        )

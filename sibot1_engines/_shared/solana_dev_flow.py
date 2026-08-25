from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote as urlquote

import requests


_RUGCHECK_REPORT_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
_DEFAULT_RPC = "https://api.mainnet-beta.solana.com"


@dataclass(frozen=True, slots=True)
class DeveloperFlowEvidence:
    known: bool
    selling: bool
    dev_wallet: str | None
    source: str
    reason: str
    checked_at_ms: int
    coverage_complete: bool = False
    outbound_unclassified: bool = False
    mint_authority_present: bool | None = None
    freeze_authority_present: bool | None = None
    lp_locked_pct: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "dev_selling_known": self.known,
            "dev_selling": self.selling,
            "dev_wallet": self.dev_wallet or "",
            "dev_selling_source": self.source,
            "dev_selling_reason": self.reason,
            "dev_selling_checked_at_ms": self.checked_at_ms,
            "dev_selling_coverage_complete": self.coverage_complete,
            "dev_outbound_unclassified": self.outbound_unclassified,
            "mint_authority_present": self.mint_authority_present,
            "freeze_authority_present": self.freeze_authority_present,
            "lp_locked_pct": self.lp_locked_pct,
        }


class SolanaDeveloperFlowResolver:
    """Low-cost, fail-closed developer-selling evidence for Solana mints.

    RugCheck's full report supplies a candidate creator address. The address is
    not trusted by itself: it must own/have owned a token account for the mint.
    Recent confirmed transactions are then inspected using JSON-RPC balance
    deltas. A mint-token decrease counts as a confirmed sale only when the same
    owner receives native SOL or another SPL token in the same transaction.

    A plain outgoing transfer is *not* labelled a sale; it makes the result
    unknown so Grok remains blocked rather than receiving a false-safe signal.
    """

    def __init__(
        self,
        *,
        rpc_url: str | None = None,
        cache_seconds: int = 90,
        unknown_cache_seconds: int = 20,
        lookback_seconds: int = 30 * 60,
        max_signatures: int = 12,
        timeout_seconds: float = 2.5,
    ):
        self.rpc_url = str(rpc_url or os.environ.get("SOLANA_RPC_URL") or _DEFAULT_RPC).strip()
        self.cache_seconds = max(30, int(cache_seconds))
        self.unknown_cache_seconds = max(5, min(int(unknown_cache_seconds), self.cache_seconds))
        self.lookback_seconds = max(60, int(lookback_seconds))
        self.max_signatures = max(3, min(25, int(max_signatures)))
        self.timeout_seconds = max(0.5, min(float(timeout_seconds), 5.0))
        self._cache: dict[str, tuple[float, DeveloperFlowEvidence]] = {}

    def _cached(self, mint: str) -> DeveloperFlowEvidence | None:
        item = self._cache.get(mint)
        if not item:
            return None
        expiry, value = item
        if time.monotonic() >= expiry:
            self._cache.pop(mint, None)
            return None
        return value

    def _store(self, mint: str, value: DeveloperFlowEvidence) -> DeveloperFlowEvidence:
        ttl = self.cache_seconds if value.known else self.unknown_cache_seconds
        self._cache[mint] = (time.monotonic() + ttl, value)
        if len(self._cache) > 1024:
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return value

    def _unknown(
        self,
        mint: str,
        reason: str,
        *,
        dev_wallet: str | None = None,
        source: str = "unknown",
        coverage_complete: bool = False,
        outbound_unclassified: bool = False,
        mint_authority_present: bool | None = None,
        freeze_authority_present: bool | None = None,
        lp_locked_pct: str | None = None,
    ) -> DeveloperFlowEvidence:
        return self._store(
            mint,
            DeveloperFlowEvidence(
                known=False,
                selling=False,
                dev_wallet=dev_wallet,
                source=source,
                reason=reason,
                checked_at_ms=int(time.time() * 1000),
                coverage_complete=coverage_complete,
                outbound_unclassified=outbound_unclassified,
                mint_authority_present=mint_authority_present,
                freeze_authority_present=freeze_authority_present,
                lp_locked_pct=lp_locked_pct,
            ),
        )

    def _rugcheck_report(self, mint: str) -> dict[str, Any]:
        response = requests.get(
            _RUGCHECK_REPORT_URL.format(mint=urlquote(mint, safe="")),
            timeout=self.timeout_seconds,
            headers={"Accept": "application/json", "User-Agent": "boot-trading-bot/sibot1-dev-flow"},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("invalid RugCheck report")
        return body

    def _rpc(self, method: str, params: list[Any]) -> Any:
        response = requests.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=self.timeout_seconds,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("error"):
            raise RuntimeError("Solana RPC returned an error")
        return body.get("result")

    @staticmethod
    def _authority_fields(report: dict[str, Any]) -> tuple[bool | None, bool | None, str | None]:
        mint_authority = bool(report.get("mintAuthority")) if "mintAuthority" in report else None
        freeze_authority = bool(report.get("freezeAuthority")) if "freezeAuthority" in report else None
        raw_lp = report.get("lpLockedPct")
        lp_locked = str(raw_lp) if raw_lp not in (None, "") else None
        return mint_authority, freeze_authority, lp_locked

    @staticmethod
    def _owner_token_amounts(meta: dict[str, Any], owner: str, stage: str) -> dict[str, int]:
        rows = meta.get("preTokenBalances" if stage == "pre" else "postTokenBalances") or []
        out: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict) or str(row.get("owner") or "") != owner:
                continue
            mint = str(row.get("mint") or "")
            if not mint:
                continue
            try:
                raw = int(str((row.get("uiTokenAmount") or {}).get("amount") or "0"))
            except Exception:
                raw = 0
            out[mint] = out.get(mint, 0) + raw
        return out

    @staticmethod
    def _account_keys(tx: dict[str, Any]) -> list[str]:
        message = (((tx.get("transaction") or {}).get("message")) or {}) if isinstance(tx, dict) else {}
        out: list[str] = []
        for row in message.get("accountKeys") or []:
            if isinstance(row, dict):
                out.append(str(row.get("pubkey") or ""))
            else:
                out.append(str(row or ""))
        return out

    def _classify_transaction(self, tx: dict[str, Any], *, mint: str, owner: str) -> tuple[bool, bool]:
        """Return (confirmed_sale, unclassified_outbound)."""
        if not isinstance(tx, dict):
            return False, False
        meta = tx.get("meta") or {}
        if not isinstance(meta, dict) or meta.get("err") is not None:
            return False, False
        pre = self._owner_token_amounts(meta, owner, "pre")
        post = self._owner_token_amounts(meta, owner, "post")
        target_before = pre.get(mint, 0)
        target_after = post.get(mint, 0)
        if target_after >= target_before:
            return False, False

        # Counter-asset SPL increase owned by the same developer.
        other_mints = (set(pre) | set(post)) - {mint}
        token_proceeds = any(post.get(other, 0) > pre.get(other, 0) for other in other_mints)

        # Native SOL increase owned by the same developer. A positive *net* delta
        # is strong evidence because transaction fees are already included.
        native_proceeds = False
        keys = self._account_keys(tx)
        if owner in keys:
            idx = keys.index(owner)
            pre_balances = meta.get("preBalances") or []
            post_balances = meta.get("postBalances") or []
            if idx < len(pre_balances) and idx < len(post_balances):
                try:
                    native_proceeds = int(post_balances[idx]) > int(pre_balances[idx])
                except Exception:
                    native_proceeds = False

        if token_proceeds or native_proceeds:
            return True, False
        return False, True

    def resolve(self, mint: str) -> DeveloperFlowEvidence:
        mint = str(mint or "").strip()
        if not mint:
            return DeveloperFlowEvidence(False, False, None, "invalid", "missing mint", int(time.time() * 1000))
        cached = self._cached(mint)
        if cached is not None:
            return cached

        try:
            report = self._rugcheck_report(mint)
        except Exception as exc:
            return self._unknown(mint, f"rugcheck_report_unavailable:{type(exc).__name__}", source="rugcheck")

        mint_authority, freeze_authority, lp_locked = self._authority_fields(report)
        creator = str(report.get("creator") or "").strip()
        if not creator:
            return self._unknown(
                mint,
                "creator_unknown",
                source="rugcheck",
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            )

        # RugCheck creator metadata is only a candidate identity. Requiring a
        # token account for this mint prevents a missing/wrong creator field from
        # being converted into false-safe developer-flow evidence.
        try:
            accounts = self._rpc(
                "getTokenAccountsByOwner",
                [creator, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
        except Exception as exc:
            return self._unknown(
                mint,
                f"creator_token_account_check_failed:{type(exc).__name__}",
                dev_wallet=creator,
                source="rugcheck+rpc",
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            )
        if not isinstance(accounts, dict) or not (accounts.get("value") or []):
            return self._unknown(
                mint,
                "creator_token_account_missing",
                dev_wallet=creator,
                source="rugcheck+rpc",
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            )

        try:
            signatures = self._rpc(
                "getSignaturesForAddress",
                [creator, {"limit": self.max_signatures, "commitment": "confirmed"}],
            )
        except Exception as exc:
            return self._unknown(
                mint,
                f"signature_history_failed:{type(exc).__name__}",
                dev_wallet=creator,
                source="rugcheck+rpc",
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            )
        if not isinstance(signatures, list):
            return self._unknown(
                mint,
                "signature_history_invalid",
                dev_wallet=creator,
                source="rugcheck+rpc",
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            )

        cutoff = int(time.time()) - self.lookback_seconds
        active: list[dict[str, Any]] = []
        for row in signatures:
            if not isinstance(row, dict) or row.get("err") is not None:
                continue
            block_time = row.get("blockTime")
            if block_time is None or int(block_time) >= cutoff:
                active.append(row)

        # We may only declare "not selling" when the bounded signature page
        # covers the entire lookback window. High-activity creators remain
        # unknown rather than being falsely cleared.
        coverage_complete = len(signatures) < self.max_signatures
        if signatures:
            known_times = [int(r.get("blockTime")) for r in signatures if isinstance(r, dict) and r.get("blockTime") is not None]
            if known_times and min(known_times) <= cutoff:
                coverage_complete = True

        tx_failure = False
        outbound_unclassified = False
        for row in active:
            signature = str(row.get("signature") or "").strip()
            if not signature:
                continue
            try:
                tx = self._rpc(
                    "getTransaction",
                    [
                        signature,
                        {
                            "encoding": "jsonParsed",
                            "commitment": "confirmed",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                )
            except Exception:
                tx_failure = True
                continue
            sale, unclassified = self._classify_transaction(tx or {}, mint=mint, owner=creator)
            if sale:
                return self._store(
                    mint,
                    DeveloperFlowEvidence(
                        known=True,
                        selling=True,
                        dev_wallet=creator,
                        source="rugcheck_creator+rpc_balance_delta",
                        reason="confirmed developer mint decrease with counter-asset proceeds",
                        checked_at_ms=int(time.time() * 1000),
                        coverage_complete=coverage_complete,
                        mint_authority_present=mint_authority,
                        freeze_authority_present=freeze_authority,
                        lp_locked_pct=lp_locked,
                    ),
                )
            outbound_unclassified = outbound_unclassified or unclassified

        if tx_failure:
            return self._unknown(
                mint,
                "transaction_history_incomplete",
                dev_wallet=creator,
                source="rugcheck+rpc",
                coverage_complete=False,
                outbound_unclassified=outbound_unclassified,
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            )
        if outbound_unclassified:
            return self._unknown(
                mint,
                "developer_outbound_transfer_unclassified",
                dev_wallet=creator,
                source="rugcheck+rpc_balance_delta",
                coverage_complete=coverage_complete,
                outbound_unclassified=True,
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            )
        if not coverage_complete:
            return self._unknown(
                mint,
                "developer_history_window_not_fully_covered",
                dev_wallet=creator,
                source="rugcheck+rpc",
                coverage_complete=False,
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            )

        return self._store(
            mint,
            DeveloperFlowEvidence(
                known=True,
                selling=False,
                dev_wallet=creator,
                source="rugcheck_creator+rpc_balance_delta",
                reason="lookback fully covered with no classified developer sale",
                checked_at_ms=int(time.time() * 1000),
                coverage_complete=True,
                mint_authority_present=mint_authority,
                freeze_authority_present=freeze_authority,
                lp_locked_pct=lp_locked,
            ),
        )

from __future__ import annotations

from decimal import Decimal

from . import solana_live_executor as _exec
from . import solana_sibot as _sol

_PREV_BUY = _exec.SolanaLiveExecutor.buy


def _simulate_with_wallet_snapshot(self, signed_b64: str) -> dict:
    result = _sol._rpc(
        self.app,
        "simulateTransaction",
        [
            signed_b64,
            {
                "encoding": "base64",
                "sigVerify": True,
                "commitment": "confirmed",
                # Explicitly request the signing wallet's post-simulation account
                # snapshot so reserve checks include fees and temporary account funding.
                "accounts": {
                    "encoding": "base64",
                    "addresses": [str(self.address)],
                },
            },
        ],
    ) or {}
    value = result.get("value") or {}
    if value.get("err") is not None:
        logs = value.get("logs") or []
        raise _exec.SolanaLiveError(
            f"Solana simulation failed: {value.get('err')} | {' | '.join(map(str, logs[-5:]))[:600]}"
        )

    minimum = getattr(self, "_minimum_post_buy_reserve_lamports", None)
    if minimum is not None:
        post = None
        accounts = value.get("accounts")
        if isinstance(accounts, list) and accounts:
            account = accounts[0]
            if isinstance(account, dict) and account.get("lamports") is not None:
                try:
                    post = int(account.get("lamports"))
                except Exception:
                    post = None
        if post is None:
            balances = value.get("postBalances")
            if isinstance(balances, list) and balances:
                try:
                    post = int(balances[0])
                except Exception:
                    post = None
        if post is None:
            raise _exec.SolanaLiveError(
                "Solana simulation did not return the signing wallet post-balance; reserve cannot be proven"
            )
        if post < int(minimum):
            have = Decimal(post) / Decimal(1_000_000_000)
            need = Decimal(int(minimum)) / Decimal(1_000_000_000)
            raise _exec.SolanaLiveError(
                f"BUY simulation would leave {have:.9f} SOL, below untouched reserve {need:.9f} SOL"
            )
        value["simulated_post_wallet_lamports"] = post
        value["simulated_reserve_lamports"] = int(minimum)
    return value


def _buy_with_simulated_reserve(self, output_mint, amount_sol, reserve_sol):
    reserve = Decimal(str(reserve_sol))
    self._minimum_post_buy_reserve_lamports = int(reserve * Decimal(1_000_000_000))
    try:
        return _PREV_BUY(self, output_mint, amount_sol, reserve_sol)
    finally:
        try:
            delattr(self, "_minimum_post_buy_reserve_lamports")
        except Exception:
            pass


def install():
    _exec.SolanaLiveExecutor._simulate = _simulate_with_wallet_snapshot
    _exec.SolanaLiveExecutor.buy = _buy_with_simulated_reserve
    print("[solana-simulated-reserve] post_buy_wallet_balance_required=true")


install()

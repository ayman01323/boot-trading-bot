from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from web3 import Web3

from web3.middleware import ExtraDataToPOAMiddleware

from .config import AppSettings, load_chains, load_dex_registry, load_kv_scoped
from .wallet_store import live_wallet_key, live_wallet_address as stored_live_wallet_address
from .multi_wallet_store import MultiWalletStore
from .user_registry import user_setting

V2_ROUTERS = {
    56: "0x10ED43C718714eb63d5aA57B78B54704E256024E",     # PancakeSwap V2 BSC
    1: "0xEfF92A263d31888d860bD50809A8D171709b7b1c",      # PancakeSwap V2 Ethereum
    42161: "0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb",  # PancakeSwap V2 Arbitrum
    8453: "0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb",   # PancakeSwap V2 Base
    137: "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",    # QuickSwap V2 Polygon
}

ERC20_ABI = [
    {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"allowance","stateMutability":"view","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
    {"type":"function","name":"approve","stateMutability":"nonpayable","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
    {"type":"function","name":"transfer","stateMutability":"nonpayable","inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
    {"type":"function","name":"decimals","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint8"}]},
    {"type":"function","name":"symbol","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"string"}]},
    {"type":"function","name":"deposit","stateMutability":"payable","inputs":[],"outputs":[]},
    {"type":"function","name":"withdraw","stateMutability":"nonpayable","inputs":[{"name":"wad","type":"uint256"}],"outputs":[]},
]

V2_ROUTER_ABI = [
    {"type":"function","name":"getAmountsOut","stateMutability":"view","inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],"outputs":[{"name":"amounts","type":"uint256[]"}]},
    {"type":"function","name":"swapExactETHForTokensSupportingFeeOnTransferTokens","stateMutability":"payable","inputs":[{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},{"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],"outputs":[]},
    {"type":"function","name":"swapExactTokensForETHSupportingFeeOnTransferTokens","stateMutability":"nonpayable","inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},{"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],"outputs":[]},
    {"type":"function","name":"swapExactTokensForTokensSupportingFeeOnTransferTokens","stateMutability":"nonpayable","inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},{"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],"outputs":[]},
]

V3_FACTORY_ABI = [{
    "type":"function","name":"getPool","stateMutability":"view",
    "inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"fee","type":"uint24"}],
    "outputs":[{"name":"pool","type":"address"}],
}]
V3_QUOTER_ABI = [{
    "type":"function","name":"quoteExactInput","stateMutability":"nonpayable",
    "inputs":[{"name":"path","type":"bytes"},{"name":"amountIn","type":"uint256"}],
    "outputs":[{"name":"amountOut","type":"uint256"},{"name":"sqrtPriceX96AfterList","type":"uint160[]"},{"name":"initializedTicksCrossedList","type":"uint32[]"},{"name":"gasEstimate","type":"uint256"}],
}]
V3_ROUTER_ABI = [{
    "type":"function","name":"exactInput","stateMutability":"payable",
    "inputs":[{"name":"params","type":"tuple","components":[
        {"name":"path","type":"bytes"},{"name":"recipient","type":"address"},{"name":"deadline","type":"uint256"},{"name":"amountIn","type":"uint256"},{"name":"amountOutMinimum","type":"uint256"}
    ]}],"outputs":[{"name":"amountOut","type":"uint256"}],
}]

def _encode_v3_path(tokens: list[str], fees: list[int]) -> bytes:
    if len(tokens) < 2 or len(fees) != len(tokens) - 1:
        raise LiveTradingError("V3 path requires one fee per hop")
    out=bytearray()
    for i,t in enumerate(tokens):
        a=Web3.to_checksum_address(t);out.extend(bytes.fromhex(a[2:]))
        if i < len(fees):
            fee=int(fees[i])
            if fee <= 0 or fee > 0xFFFFFF:raise LiveTradingError("Invalid V3 uint24 fee")
            out.extend(fee.to_bytes(3,"big"))
    return bytes(out)



class LiveTradingError(RuntimeError):
    pass


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "y"}


def _dec(v, name: str) -> Decimal:
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError) as exc:
        raise LiveTradingError(f"{name} must be a number") from exc
    if d <= 0:
        raise LiveTradingError(f"{name} must be greater than zero")
    return d


def _atomic_append(path: Path, row: dict, fieldnames: list[str], keep=5000):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    rows.append(row)
    rows = rows[-keep:]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows([{k:r.get(k, "") for k in fieldnames} for r in rows])
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


@dataclass
class Quote:
    chain_slug: str
    side: str
    token: str
    token_symbol: str
    token_decimals: int
    amount_in_human: str
    expected_out_human: str
    minimum_out_human: str
    slippage_bps: int
    router: str


class LiveTrader:
    """Local signer for explicit Telegram trades.

    v2.2.1 keeps signing separate from historical copy-after-mining behaviour and scopes signing wallets by Telegram owner.
    The private key is read only from LIVE_WALLET_PRIVATE_KEY in the server environment.
    It is never read from Telegram or written to CSV/logs.
    """

    def __init__(self, app: AppSettings, chain_slug: str, *, telegram_id=None, wallet_id=None, private_key=None, require_wallet=True, router_override=None):
        self.app = app
        self.telegram_id = None if telegram_id is None else str(telegram_id)
        self.wallet_id = wallet_id
        self.chain = next((c for c in load_chains(app, enabled_only=False) if c.slug == chain_slug.lower()), None)
        if not self.chain:
            raise LiveTradingError(f"Unknown chain: {chain_slug}")
        if not self.chain.enabled:
            raise LiveTradingError(f"Chain is disabled in CSVbot/chains.csv: {self.chain.name}")
        if self.chain.chain_id not in V2_ROUTERS:
            raise LiveTradingError(f"Live V2 execution is not configured for {self.chain.name}")
        if not self.chain.rpc_urls:
            raise LiveTradingError(f"No enabled RPC endpoint for {self.chain.name}")
        self.settings = load_kv_scoped(app.csv_dir / "live_trading_settings.csv", self.chain.chain_id)
        if self.telegram_id is not None:
            # Per-user settings override platform defaults without modifying other users.
            for _k in ["manual_buy_enabled","manual_sell_enabled","require_confirm_word","max_native_input_per_trade","slippage_bps","deadline_seconds","gas_limit_multiplier","gas_bid_multiplier","min_native_gas_reserve"]:
                _v = user_setting(app.csv_dir, self.telegram_id, self.chain.chain_id, _k, None)
                if _v is not None:
                    self.settings[_k] = str(_v)
        self.w3 = Web3(
            Web3.HTTPProvider(
                self.chain.rpc_urls[0],
                request_kwargs={"timeout": 20},
            )
        )

        if self.chain.chain_id in {56, 137}:
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if not self.w3.is_connected():
            raise LiveTradingError(f"Cannot connect to {self.chain.name} RPC")
        rpc_chain = int(self.w3.eth.chain_id)
        if rpc_chain != self.chain.chain_id:
            raise LiveTradingError(f"RPC chain mismatch: expected {self.chain.chain_id}, got {rpc_chain}")
        self.account = None
        self.address = None
        if private_key is not None:
            key = str(private_key).strip()
        elif self.telegram_id is not None:
            try:
                key = MultiWalletStore(app.data_dir, app.csv_dir).private_key_hex(self.telegram_id, self.wallet_id)
                self.wallet_id = MultiWalletStore(app.data_dir, app.csv_dir).get_meta(self.telegram_id, self.wallet_id)["wallet_id"]
            except Exception as exc:
                if require_wallet:
                    raise LiveTradingError("No valid wallet is configured for this Telegram user") from exc
                key = None
        else:
            try:
                key = live_wallet_key(app)
            except Exception as exc:
                if require_wallet:
                    raise LiveTradingError("No valid live wallet is configured on the server") from exc
                key = None
        if key:
            try:
                self.account = self.w3.eth.account.from_key(key)
                self.address = Web3.to_checksum_address(self.account.address)
            except Exception as exc:
                raise LiveTradingError("Configured wallet private key is invalid") from exc
        configured_router = (self.settings.get("router_address") or "").strip()
        if router_override:
            override = Web3.to_checksum_address(router_override)
            allowed = {
                (r.get("router") or "").strip().lower()
                for r in load_dex_registry(app.csv_dir, self.chain.chain_id)
                if (r.get("version") or "").strip().upper() == "V2"
            }
            if override.lower() not in allowed:
                raise LiveTradingError("Router override is not an enabled V2 venue in CSVbot/dex_registry.csv")
            self.router_address = override
        else:
            self.router_address = Web3.to_checksum_address(configured_router or V2_ROUTERS[self.chain.chain_id])
        if not self.w3.eth.get_code(self.router_address):
            raise LiveTradingError(f"V2 router has no contract code: {self.router_address}")
        self.router = self.w3.eth.contract(address=self.router_address, abi=V2_ROUTER_ABI)
        self.wrapped = Web3.to_checksum_address(self.chain.wrapped_base_address)

    def _require_enabled(self, side: str):
        if not _bool(self.settings.get("trading_enabled"), False):
            raise LiveTradingError("Platform LIVE trading gate is OFF. MASTER must enable it.")
        if self.telegram_id is not None:
            from .user_registry import user_bool
            if not user_bool(self.app.csv_dir, self.telegram_id, self.chain.chain_id, "live_trading_enabled", False):
                raise LiveTradingError("Your LIVE trading switch is OFF. Use /live on CONFIRM.")
        if side == "BUY" and not _bool(self.settings.get("manual_buy_enabled"), True):
            raise LiveTradingError("Manual BUY is disabled in live_trading_settings.csv")
        if side == "SELL" and not _bool(self.settings.get("manual_sell_enabled"), True):
            raise LiveTradingError("Manual SELL is disabled in live_trading_settings.csv")

    def _confirm(self, confirm: str):
        if _bool(self.settings.get("require_confirm_word"), True) and str(confirm).strip().upper() != "CONFIRM":
            raise LiveTradingError("Add CONFIRM at the end of the Telegram command")

    def _token(self, address: str):
        try:
            a = Web3.to_checksum_address(address)
        except Exception as exc:
            raise LiveTradingError("Token must be a valid 0x contract address") from exc
        if a == self.wrapped:
            raise LiveTradingError("Use a token other than the wrapped native asset")
        code = self.w3.eth.get_code(a)
        if not code:
            raise LiveTradingError("Token address has no contract code on this chain")
        c = self.w3.eth.contract(address=a, abi=ERC20_ABI)
        try:
            dec = int(c.functions.decimals().call())
        except Exception:
            dec = 18
        try:
            sym = str(c.functions.symbol().call())[:24]
        except Exception:
            sym = a[:10]
        return a, c, dec, sym

    def _slippage_bps(self) -> int:
        try:
            bps = int(float(self.settings.get("slippage_bps", "500")))
        except Exception:
            bps = 500
        if not (1 <= bps <= 5000):
            raise LiveTradingError("slippage_bps must be between 1 and 5000")
        return bps

    def _deadline(self):
        try:
            seconds = int(self.settings.get("deadline_seconds", "120"))
        except Exception:
            seconds = 120
        seconds = max(30, min(900, seconds))
        return int(time.time()) + seconds

    def _gas_bid_multiplier(self) -> float:
        """Multiplier applied to the fee *price*, not the gas limit.

        A value above 1.0 increases the priority fee on EIP-1559 style chains and
        the gasPrice on legacy-style fee markets.  It is deliberately bounded so
        a CSV/user typo cannot create an unbounded fee bid.
        """
        try:
            mult = float(self.settings.get("gas_bid_multiplier", "1.25"))
        except Exception:
            mult = 1.25
        if not (1.0 <= mult <= 3.0):
            raise LiveTradingError("gas_bid_multiplier must be between 1.0 and 3.0")
        return mult

    def _fee_fields(self, tx: dict) -> dict:
        latest = self.w3.eth.get_block("latest")
        base_fee = latest.get("baseFeePerGas")
        bid_mult = self._gas_bid_multiplier()
        if base_fee is not None:
            try:
                suggested_priority = int(self.w3.eth.max_priority_fee)
            except Exception:
                suggested_priority = max(1_000_000_000, int(base_fee) // 20)
            priority = max(1, int(suggested_priority * bid_mult))
            tx["maxPriorityFeePerGas"] = priority
            # Keep a 2x base-fee headroom while bidding the boosted priority fee.
            # simulate_cycle() uses these same fee fields in its gas/profit gate.
            tx["maxFeePerGas"] = int(base_fee) * 2 + priority
        else:
            tx["gasPrice"] = max(1, int(int(self.w3.eth.gas_price) * bid_mult))
        return tx

    def _base_tx(self, value=0):
        if not self.address or self.account is None:
            raise LiveTradingError("This operation requires a signing wallet")
        tx = {
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address, "pending"),
            "chainId": self.chain.chain_id,
            "value": int(value),
        }
        return self._fee_fields(tx)

    def _sign_send(self, tx: dict) -> str:
        if self.account is None:
            raise LiveTradingError("This operation requires a signing wallet")
        signed = self.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
        txh = self.w3.eth.send_raw_transaction(raw)
        return txh.hex()

    def _with_gas(self, fn, tx: dict):
        try:
            gas = int(fn.estimate_gas(tx))
        except Exception as exc:
            raise LiveTradingError(f"Transaction simulation/gas estimate failed: {exc}") from exc
        try:
            mult = float(self.settings.get("gas_limit_multiplier", "1.20"))
        except Exception:
            mult = 1.20
        tx["gas"] = max(80_000, int(gas * max(1.0, min(2.0, mult))))
        return tx

    def native_balance(self) -> Decimal:
        return Decimal(self.w3.eth.get_balance(self.address)) / Decimal(10**18)

    def token_balance(self, token: str):
        a, c, dec, sym = self._token(token)
        raw = int(c.functions.balanceOf(self.address).call())
        return a, c, dec, sym, raw, Decimal(raw) / Decimal(10**dec)

    def status(self) -> dict:
        return {
            "chain": self.chain.slug,
            "name": self.chain.name,
            "wallet": self.address,
            "native_symbol": self.chain.native_symbol,
            "native_balance": self.native_balance(),
            "router": self.router_address,
            "enabled": _bool(self.settings.get("trading_enabled"), False),
            "slippage_bps": self._slippage_bps(),
            "gas_bid_multiplier": self._gas_bid_multiplier(),
        }

    def quote_buy(self, token: str, amount_native) -> Quote:
        amount = _dec(amount_native, "Buy amount")
        token_a, _, dec, sym = self._token(token)
        amount_wei = int(amount * Decimal(10**18))
        try:
            amounts = self.router.functions.getAmountsOut(amount_wei, [self.wrapped, token_a]).call()
        except Exception as exc:
            raise LiveTradingError("No direct PancakeSwap V2 route/quote from wrapped native asset to this token") from exc
        expected = int(amounts[-1]); bps = self._slippage_bps(); minimum = expected * (10_000 - bps) // 10_000
        return Quote(self.chain.slug,"BUY",token_a,sym,dec,f"{amount:f}",f"{Decimal(expected)/Decimal(10**dec):f}",f"{Decimal(minimum)/Decimal(10**dec):f}",bps,self.router_address)

    def quote_sell(self, token: str, amount_spec: str) -> tuple[Quote,int]:
        token_a, c, dec, sym, balance_raw, balance = self.token_balance(token)
        spec = str(amount_spec).strip()
        if spec.endswith("%"):
            try: pct = Decimal(spec[:-1])
            except Exception as exc: raise LiveTradingError("Sell percentage must look like 25%, 50% or 100%") from exc
            if not (pct > 0 and pct <= 100): raise LiveTradingError("Sell percentage must be above 0 and at most 100%")
            amount_raw = int(Decimal(balance_raw) * pct / Decimal(100))
            amount_human = Decimal(amount_raw) / Decimal(10**dec)
        else:
            amount_human = _dec(spec, "Sell amount")
            amount_raw = int(amount_human * Decimal(10**dec))
        if amount_raw <= 0 or amount_raw > balance_raw:
            raise LiveTradingError(f"Insufficient {sym} balance; wallet has {balance:f}")
        try:
            amounts = self.router.functions.getAmountsOut(amount_raw,[token_a,self.wrapped]).call()
        except Exception as exc:
            raise LiveTradingError("No direct PancakeSwap V2 sell quote from this token to wrapped native asset") from exc
        expected = int(amounts[-1]); bps=self._slippage_bps(); minimum=expected*(10_000-bps)//10_000
        q=Quote(self.chain.slug,"SELL",token_a,sym,dec,f"{amount_human:f}",f"{Decimal(expected)/Decimal(10**18):f}",f"{Decimal(minimum)/Decimal(10**18):f}",bps,self.router_address)
        return q, amount_raw

    def buy(self, token: str, amount_native, confirm: str) -> dict:
        self._require_enabled("BUY"); self._confirm(confirm)
        q = self.quote_buy(token, amount_native)
        amount = _dec(amount_native,"Buy amount")
        try: max_input = Decimal(self.settings.get("max_native_input_per_trade","0.05"))
        except Exception: max_input = Decimal("0.05")
        if amount > max_input:
            raise LiveTradingError(f"Buy exceeds max_native_input_per_trade={max_input} {self.chain.native_symbol}")
        try: reserve = Decimal(self.settings.get("min_native_gas_reserve","0.005"))
        except Exception: reserve=Decimal("0.005")
        if self.native_balance() < amount + reserve:
            raise LiveTradingError(f"Keep at least {reserve} {self.chain.native_symbol} as gas reserve")
        token_a=Web3.to_checksum_address(q.token); amount_wei=int(amount*Decimal(10**18)); min_out=int(Decimal(q.minimum_out_human)*Decimal(10**q.token_decimals))
        fn=self.router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(min_out,[self.wrapped,token_a],self.address,self._deadline())
        tx=self._base_tx(value=amount_wei); tx=self._with_gas(fn,tx); built=fn.build_transaction(tx)
        tx_hash=self._sign_send(built)
        self._audit("BUY",token_a,q.token_symbol,q.amount_in_human,q.expected_out_human,q.minimum_out_human,tx_hash,"BROADCAST")
        return {"tx_hash":tx_hash,"quote":q,"explorer":f"{self.chain.explorer_url}/tx/{tx_hash}"}

    def _send_approval(self, token_contract, amount_raw: int, spender=None) -> str:
        spender=Web3.to_checksum_address(spender or self.router_address)
        fn=token_contract.functions.approve(spender,amount_raw)
        tx=self._base_tx();tx=self._with_gas(fn,tx);built=fn.build_transaction(tx);txh=self._sign_send(built)
        try:
            receipt=self.w3.eth.wait_for_transaction_receipt(txh,timeout=120,poll_latency=2)
        except Exception as exc:
            raise LiveTradingError(f"Approval broadcast {txh} but confirmation timed out; check explorer before retrying") from exc
        if int(receipt.status)!=1:raise LiveTradingError(f"Token approval failed: {txh}")
        return txh

    def _ensure_approval(self, token_contract, token_address, amount_raw: int) -> str | None:
        allowance=int(token_contract.functions.allowance(self.address,self.router_address).call())
        if allowance>=amount_raw:return None
        # Some ERC-20s require allowance to be reset to zero before a new non-zero value.
        if allowance>0:self._send_approval(token_contract,0)
        return self._send_approval(token_contract,amount_raw)

    def sell(self, token: str, amount_spec: str, confirm: str) -> dict:
        self._require_enabled("SELL");self._confirm(confirm)
        q,amount_raw=self.quote_sell(token,amount_spec)
        token_a,c,dec,sym,_,_=self.token_balance(token)
        approval_hash=self._ensure_approval(c,token_a,amount_raw)
        min_out=int(Decimal(q.minimum_out_human)*Decimal(10**18))
        fn=self.router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(amount_raw,min_out,[token_a,self.wrapped],self.address,self._deadline())
        tx=self._base_tx();tx=self._with_gas(fn,tx);built=fn.build_transaction(tx);tx_hash=self._sign_send(built)
        self._audit("SELL",token_a,sym,q.amount_in_human,q.expected_out_human,q.minimum_out_human,tx_hash,"BROADCAST",approval_hash or "")
        return {"tx_hash":tx_hash,"approval_hash":approval_hash,"quote":q,"explorer":f"{self.chain.explorer_url}/tx/{tx_hash}"}

    def wrapped_balance(self) -> Decimal:
        c = self.w3.eth.contract(address=self.wrapped, abi=ERC20_ABI)
        raw = int(c.functions.balanceOf(self.address).call())
        return Decimal(raw) / Decimal(10**18)

    def wrap_native(self, amount_native, confirm: str = "CONFIRM") -> dict:
        self._confirm(confirm)
        amount = _dec(amount_native, "Wrap amount")
        try: reserve = Decimal(self.settings.get("min_native_gas_reserve", "0.005"))
        except Exception: reserve = Decimal("0.005")
        if self.native_balance() < amount + reserve:
            raise LiveTradingError(f"Insufficient {self.chain.native_symbol}; keep {reserve} for gas")
        c = self.w3.eth.contract(address=self.wrapped, abi=ERC20_ABI)
        fn = c.functions.deposit()
        tx = self._base_tx(value=int(amount * Decimal(10**18)))
        tx = self._with_gas(fn, tx)
        txh = self._sign_send(fn.build_transaction(tx))
        return {"tx_hash": txh, "explorer": f"{self.chain.explorer_url}/tx/{txh}"}

    def _auto_execution_routers(self) -> list[str]:
        """Enabled bounded-approval routers. Cross-DEX/shadow venues are deliberately excluded."""
        out=[self.router_address]
        for r in load_dex_registry(self.app.csv_dir,self.chain.chain_id):
            if not _bool(r.get("auto_execute"), (r.get("version") or "").strip().upper()=="V2"):
                continue
            if (r.get("version") or "").strip().upper() not in {"V2","V3"}:continue
            a=(r.get("router") or "").strip()
            if Web3.is_address(a):
                a=Web3.to_checksum_address(a)
                if a not in out:out.append(a)
        return out

    def approve_wrapped_cap_for(self, spender, amount_native, confirm: str = "CONFIRM") -> dict:
        self._confirm(confirm);spender=Web3.to_checksum_address(spender);amount=_dec(amount_native,"Approval amount");raw=int(amount*Decimal(10**18));c=self.w3.eth.contract(address=self.wrapped,abi=ERC20_ABI)
        current=int(c.functions.allowance(self.address,spender).call());txh=None
        if current!=raw:
            if current>0:self._send_approval(c,0,spender)
            txh=self._send_approval(c,raw,spender)
        return {"approval_hash":txh,"allowance":amount,"spender":spender}

    def prepare_auto(self, amount_native, confirm: str = "CONFIRM") -> dict:
        """Wrap capital and exact-approve every enabled LIVE V2/V3 router; never unlimited."""
        self._confirm(confirm);amount=_dec(amount_native,"Auto capital");wrap_hash=None;current=self.wrapped_balance()
        if current<amount:
            needed=amount-current;r=self.wrap_native(needed,"CONFIRM");wrap_hash=r["tx_hash"]
            try:receipt=self.w3.eth.wait_for_transaction_receipt(wrap_hash,timeout=120,poll_latency=2)
            except Exception as exc:raise LiveTradingError(f"Wrap broadcast {wrap_hash} but confirmation timed out") from exc
            if int(receipt.status)!=1:raise LiveTradingError(f"Wrapped-native funding failed: {wrap_hash}")
        approvals=[]
        for spender in self._auto_execution_routers():
            approvals.append(self.approve_wrapped_cap_for(spender,amount,"CONFIRM"))
        return {"wrap_hash":wrap_hash,"approval_hash":next((x.get("approval_hash") for x in approvals if x.get("approval_hash")),None),"approvals":approvals,"prepared_amount":amount,"wrapped_balance":self.wrapped_balance()}

    def approve_wrapped_cap(self, amount_native, confirm: str = "CONFIRM") -> dict:
        return self.approve_wrapped_cap_for(self.router_address,amount_native,confirm)

    def cycle_quote(self, path: list[str], amount_native) -> dict:
        amount = _dec(amount_native, "Cycle input")
        if len(path) < 3 or len(path) > 8:
            raise LiveTradingError("Cycle path must contain 3 to 8 addresses including wrapped-native start/end")
        try:
            pp = [Web3.to_checksum_address(x) for x in path]
        except Exception as exc:
            raise LiveTradingError("Cycle path contains an invalid address") from exc
        if pp[0] != self.wrapped or pp[-1] != self.wrapped:
            raise LiveTradingError("Cycle path must start and end with the wrapped native token")
        amount_raw = int(amount * Decimal(10**18))
        try:
            amounts = self.router.functions.getAmountsOut(amount_raw, pp).call()
        except Exception as exc:
            raise LiveTradingError("Exact V2 route quote failed; one or more pools may not exist") from exc
        out_raw = int(amounts[-1])
        return {
            "path": pp, "amount_in_raw": amount_raw, "amount_out_raw": out_raw,
            "amount_in": amount, "amount_out": Decimal(out_raw) / Decimal(10**18),
            "gross_profit": Decimal(out_raw - amount_raw) / Decimal(10**18),
        }

    def simulate_cycle(self, path: list[str], amount_native, min_net_profit_native) -> dict:
        q = self.cycle_quote(path, amount_native)
        min_profit = Decimal(str(min_net_profit_native))
        if min_profit < 0:
            raise LiveTradingError("Minimum net profit cannot be negative")
        wrapped_c = self.w3.eth.contract(address=self.wrapped, abi=ERC20_ABI)
        bal = int(wrapped_c.functions.balanceOf(self.address).call())
        allowance = int(wrapped_c.functions.allowance(self.address, self.router_address).call())
        prepared = bal >= q["amount_in_raw"] and allowance >= q["amount_in_raw"]
        if not prepared:
            return {**q, "prepared": False, "simulation_ok": False, "gas": 0, "gas_cost_base": Decimal(0), "min_out_raw": 0, "reason": "wallet needs WBNB/WETH capital and router allowance; use /autoprep"}
        fn0 = self.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
            q["amount_in_raw"], q["amount_in_raw"], q["path"], self.address, self._deadline()
        )
        tx0 = self._base_tx()
        try:
            gas0 = int(fn0.estimate_gas(tx0))
        except Exception as exc:
            return {**q, "prepared": True, "simulation_ok": False, "gas": 0, "gas_cost_base": Decimal(0), "min_out_raw": 0, "reason": f"route simulation failed: {exc}"}
        fee_per_gas = int(tx0.get("maxFeePerGas") or tx0.get("gasPrice") or self.w3.eth.gas_price)
        # Reserve 30% above estimated gas at maxFeePerGas/gasPrice. This reserve is
        # part of the output floor, so a route cannot deliberately execute unless
        # it repays capital + conservative gas reserve + configured minimum net.
        reserve_gas_units = int(gas0 * 1.30)
        gas_cost = Decimal(reserve_gas_units) * Decimal(fee_per_gas) / Decimal(10**18)
        min_out = q["amount_in_raw"] + int((gas_cost + min_profit) * Decimal(10**18))
        if q["amount_out_raw"] <= min_out:
            return {**q, "prepared": True, "simulation_ok": False, "gas": gas0, "gas_cost_base": gas_cost, "min_out_raw": min_out, "reason": "quoted gross edge does not cover conservative gas reserve plus minimum net profit"}
        fn = self.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
            q["amount_in_raw"], min_out, q["path"], self.address, self._deadline()
        )
        try:
            gas = int(fn.estimate_gas(self._base_tx()))
        except Exception as exc:
            return {**q, "prepared": True, "simulation_ok": False, "gas": gas0, "gas_cost_base": gas_cost, "min_out_raw": min_out, "reason": f"profit-protected simulation failed: {exc}"}
        if gas > gas0:
            reserve_gas_units = int(gas * 1.30)
            gas_cost = Decimal(reserve_gas_units) * Decimal(fee_per_gas) / Decimal(10**18)
            min_out = q["amount_in_raw"] + int((gas_cost + min_profit) * Decimal(10**18))
            if q["amount_out_raw"] <= min_out:
                return {**q, "prepared": True, "simulation_ok": False, "gas": gas, "gas_cost_base": gas_cost, "min_out_raw": min_out, "reason": "re-estimated gas reserve removes the net-profit edge"}
            fn = self.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
                q["amount_in_raw"], min_out, q["path"], self.address, self._deadline()
            )
            try:
                gas = int(fn.estimate_gas(self._base_tx()))
            except Exception as exc:
                return {**q, "prepared": True, "simulation_ok": False, "gas": gas, "gas_cost_base": gas_cost, "min_out_raw": min_out, "reason": f"final profit-protected simulation failed: {exc}"}
        return {**q, "prepared": True, "simulation_ok": True, "gas": gas, "gas_cost_base": gas_cost, "min_out_raw": min_out, "reason": "PASS"}

    def _prebroadcast_cycle(self, path: list[str], amount_native, min_net_profit_native) -> tuple[dict, dict]:
        """Build and locally execute the exact transaction immediately before signing.

        This is a mandatory fail-closed gate for AUTO cycles.  It deliberately repeats
        the wallet-specific quote/gas simulation, then executes the *exact built
        transaction* through eth_call.  eth_call does not broadcast or change chain
        state.  Any revert/RPC simulation failure prevents signing.
        """
        sim = self.simulate_cycle(path, amount_native, min_net_profit_native)
        if not sim.get("simulation_ok"):
            raise LiveTradingError(sim.get("reason") or "Cycle simulation failed")

        fn = self.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
            sim["amount_in_raw"], sim["min_out_raw"], sim["path"], self.address, self._deadline()
        )
        tx = self._base_tx()
        tx["gas"] = max(100000, int(sim["gas"] * 1.15))
        built = fn.build_transaction(tx)

        # RPC eth_call transaction objects do not need signing metadata.  Keep only
        # fields relevant to EVM execution for widest node compatibility.
        call_keys = {
            "from", "to", "gas", "gasPrice", "maxFeePerGas",
            "maxPriorityFeePerGas", "value", "data", "accessList", "type"
        }
        call_tx = {k: v for k, v in built.items() if k in call_keys}
        try:
            self.w3.eth.call(call_tx)
        except Exception as exc:
            try:
                self._audit(
                    "AUTO_PREFLIGHT", ">".join(sim["path"]), "ROUTE",
                    str(sim["amount_in"]), str(sim["amount_out"]),
                    str(Decimal(sim["min_out_raw"]) / Decimal(10**18)),
                    "", "REJECTED"
                )
            except Exception:
                pass
            raise LiveTradingError(f"Mandatory pre-broadcast eth_call simulation failed: {exc}") from exc

        self._audit(
            "AUTO_PREFLIGHT", ">".join(sim["path"]), "ROUTE",
            str(sim["amount_in"]), str(sim["amount_out"]),
            str(Decimal(sim["min_out_raw"]) / Decimal(10**18)),
            "", "PASS"
        )
        return {**sim, "preflight_call_ok": True}, built

    def preflight_cycle(self, path: list[str], amount_native, min_net_profit_native) -> dict:
        """Public no-broadcast AUTO preflight using the same gate as execute_cycle."""
        sim, _ = self._prebroadcast_cycle(path, amount_native, min_net_profit_native)
        return sim

    def execute_cycle(self, path: list[str], amount_native, min_net_profit_native, confirm: str = "CONFIRM") -> dict:
        self._require_enabled("BUY")
        self._confirm(confirm)

        # Mandatory final simulation occurs here, immediately before _sign_send.
        # The caller may already have simulated once; AUTO therefore gets both the
        # selection-time simulation and this final exact-transaction preflight.
        sim, built = self._prebroadcast_cycle(path, amount_native, min_net_profit_native)
        txh = self._sign_send(built)
        self._audit(
            "AUTO_CYCLE", ">".join(sim["path"]), "ROUTE",
            str(sim["amount_in"]), str(sim["amount_out"]),
            str(Decimal(sim["min_out_raw"]) / Decimal(10**18)),
            txh, "BROADCAST"
        )
        return {**sim, "tx_hash": txh, "explorer": f"{self.chain.explorer_url}/tx/{txh}"}

    def _v3_venue(self, router_address, quoter_address, *, require_auto=False) -> dict:
        router=Web3.to_checksum_address(router_address);quoter=Web3.to_checksum_address(quoter_address)
        for r in load_dex_registry(self.app.csv_dir,self.chain.chain_id):
            if (r.get("version") or "").strip().upper()!="V3":continue
            if (r.get("router") or "").strip().lower()!=router.lower():continue
            if (r.get("quoter") or "").strip().lower()!=quoter.lower():continue
            if require_auto and not _bool(r.get("auto_execute"),False):
                raise LiveTradingError("This V3 venue is scan-only; automatic signing is disabled")
            return r
        raise LiveTradingError("V3 router/quoter is not an enabled venue in CSVbot/dex_registry.csv")

    def v3_cycle_quote(self, path: list[str], fees: list[int], amount_native, router_address, quoter_address) -> dict:
        amount=_dec(amount_native,"V3 cycle input")
        if len(path)<3 or len(path)>8:raise LiveTradingError("V3 cycle path must contain 3 to 8 addresses")
        pp=[Web3.to_checksum_address(x) for x in path]
        if pp[0]!=self.wrapped or pp[-1]!=self.wrapped:raise LiveTradingError("V3 cycle must start and end with wrapped native")
        ff=[int(x) for x in fees]
        if len(ff)!=len(pp)-1:raise LiveTradingError("V3 route fee count does not match hops")
        self._v3_venue(router_address,quoter_address,require_auto=False)
        router=Web3.to_checksum_address(router_address);quoter=Web3.to_checksum_address(quoter_address)
        if not self.w3.eth.get_code(router) or not self.w3.eth.get_code(quoter):raise LiveTradingError("V3 router/quoter has no contract code")
        packed=_encode_v3_path(pp,ff);raw=int(amount*Decimal(10**18));qc=self.w3.eth.contract(address=quoter,abi=V3_QUOTER_ABI)
        try:res=qc.functions.quoteExactInput(packed,raw).call()
        except Exception as exc:raise LiveTradingError(f"Exact V3 route quote failed: {exc}") from exc
        out_raw=int(res[0] if isinstance(res,(list,tuple)) else res)
        return {"path":pp,"fees":ff,"packed_path":packed,"router_address":router,"quoter_address":quoter,"amount_in_raw":raw,"amount_out_raw":out_raw,"amount_in":amount,"amount_out":Decimal(out_raw)/Decimal(10**18),"gross_profit":Decimal(out_raw-raw)/Decimal(10**18),"quoter_gas":int(res[3]) if isinstance(res,(list,tuple)) and len(res)>3 else 0}

    def simulate_v3_cycle(self, path:list[str], fees:list[int], amount_native, min_net_profit_native, router_address, quoter_address) -> dict:
        q=self.v3_cycle_quote(path,fees,amount_native,router_address,quoter_address);min_profit=Decimal(str(min_net_profit_native))
        if min_profit<0:raise LiveTradingError("Minimum net profit cannot be negative")
        router=Web3.to_checksum_address(router_address);wrapped_c=self.w3.eth.contract(address=self.wrapped,abi=ERC20_ABI);bal=int(wrapped_c.functions.balanceOf(self.address).call());allowance=int(wrapped_c.functions.allowance(self.address,router).call());prepared=bal>=q["amount_in_raw"] and allowance>=q["amount_in_raw"]
        if not prepared:return {**q,"prepared":False,"simulation_ok":False,"gas":0,"gas_cost_base":Decimal(0),"min_out_raw":0,"reason":"wallet needs wrapped capital and V3 router allowance; use /autoprep again"}
        rc=self.w3.eth.contract(address=router,abi=V3_ROUTER_ABI)
        def fn_for(min_out):return rc.functions.exactInput((q["packed_path"],self.address,self._deadline(),q["amount_in_raw"],int(min_out)))
        tx0=self._base_tx();fn0=fn_for(q["amount_in_raw"])
        try:gas0=int(fn0.estimate_gas(tx0))
        except Exception as exc:return {**q,"prepared":True,"simulation_ok":False,"gas":0,"gas_cost_base":Decimal(0),"min_out_raw":0,"reason":f"V3 route simulation failed: {exc}"}
        fee_per_gas=int(tx0.get("maxFeePerGas") or tx0.get("gasPrice") or self.w3.eth.gas_price);reserve_units=int(gas0*1.30);gas_cost=Decimal(reserve_units)*Decimal(fee_per_gas)/Decimal(10**18);min_out=q["amount_in_raw"]+int((gas_cost+min_profit)*Decimal(10**18))
        if q["amount_out_raw"]<=min_out:return {**q,"prepared":True,"simulation_ok":False,"gas":gas0,"gas_cost_base":gas_cost,"min_out_raw":min_out,"reason":"V3 quoted edge does not cover conservative gas reserve plus minimum net profit"}
        fn=fn_for(min_out)
        try:gas=int(fn.estimate_gas(self._base_tx()))
        except Exception as exc:return {**q,"prepared":True,"simulation_ok":False,"gas":gas0,"gas_cost_base":gas_cost,"min_out_raw":min_out,"reason":f"V3 profit-protected simulation failed: {exc}"}
        if gas>gas0:
            reserve_units=int(gas*1.30);gas_cost=Decimal(reserve_units)*Decimal(fee_per_gas)/Decimal(10**18);min_out=q["amount_in_raw"]+int((gas_cost+min_profit)*Decimal(10**18))
            if q["amount_out_raw"]<=min_out:return {**q,"prepared":True,"simulation_ok":False,"gas":gas,"gas_cost_base":gas_cost,"min_out_raw":min_out,"reason":"V3 re-estimated gas reserve removes the net-profit edge"}
            fn=fn_for(min_out)
            try:gas=int(fn.estimate_gas(self._base_tx()))
            except Exception as exc:return {**q,"prepared":True,"simulation_ok":False,"gas":gas,"gas_cost_base":gas_cost,"min_out_raw":min_out,"reason":f"V3 final simulation failed: {exc}"}
        return {**q,"prepared":True,"simulation_ok":True,"gas":gas,"gas_cost_base":gas_cost,"min_out_raw":min_out,"reason":"PASS"}

    def _prebroadcast_v3_cycle(self,path,fees,amount_native,min_net_profit_native,router_address,quoter_address):
        self._v3_venue(router_address,quoter_address,require_auto=True);sim=self.simulate_v3_cycle(path,fees,amount_native,min_net_profit_native,router_address,quoter_address)
        if not sim.get("simulation_ok"):raise LiveTradingError(sim.get("reason") or "V3 simulation failed")
        rc=self.w3.eth.contract(address=Web3.to_checksum_address(router_address),abi=V3_ROUTER_ABI);fn=rc.functions.exactInput((sim["packed_path"],self.address,self._deadline(),sim["amount_in_raw"],sim["min_out_raw"]));tx=self._base_tx();tx["gas"]=max(120000,int(sim["gas"]*1.15));built=fn.build_transaction(tx);keys={"from","to","gas","gasPrice","maxFeePerGas","maxPriorityFeePerGas","value","data","accessList","type"};call_tx={k:v for k,v in built.items() if k in keys}
        try:self.w3.eth.call(call_tx)
        except Exception as exc:
            try:self._audit("AUTO_PREFLIGHT_V3",">".join(sim["path"]),"V3_ROUTE",str(sim["amount_in"]),str(sim["amount_out"]),str(Decimal(sim["min_out_raw"])/Decimal(10**18)),"","REJECTED")
            except Exception:pass
            raise LiveTradingError(f"Mandatory V3 pre-broadcast eth_call failed: {exc}") from exc
        self._audit("AUTO_PREFLIGHT_V3",">".join(sim["path"]),"V3_ROUTE",str(sim["amount_in"]),str(sim["amount_out"]),str(Decimal(sim["min_out_raw"])/Decimal(10**18)),"","PASS");return {**sim,"preflight_call_ok":True},built

    def execute_v3_cycle(self,path,fees,amount_native,min_net_profit_native,router_address,quoter_address,confirm="CONFIRM") -> dict:
        self._require_enabled("BUY");self._confirm(confirm);sim,built=self._prebroadcast_v3_cycle(path,fees,amount_native,min_net_profit_native,router_address,quoter_address);txh=self._sign_send(built);self._audit("AUTO_CYCLE_V3",">".join(sim["path"]),"V3_ROUTE",str(sim["amount_in"]),str(sim["amount_out"]),str(Decimal(sim["min_out_raw"])/Decimal(10**18)),txh,"BROADCAST");return {**sim,"tx_hash":txh,"explorer":f"{self.chain.explorer_url}/tx/{txh}"}

    def transfer_native(self, to_address: str, amount_native, confirm: str = "CONFIRM") -> dict:
        self._confirm(confirm)
        if not self.address:
            raise LiveTradingError("No signing wallet")
        try:
            to = Web3.to_checksum_address(to_address)
        except Exception as exc:
            raise LiveTradingError("Destination must be a valid 0x address") from exc
        amount = _dec(amount_native, "Transfer amount")
        try: reserve = Decimal(self.settings.get("min_native_gas_reserve", "0.005"))
        except Exception: reserve = Decimal("0.005")
        if self.native_balance() < amount + reserve:
            raise LiveTradingError(f"Insufficient {self.chain.native_symbol}; keep {reserve} for gas")
        tx = self._base_tx(value=int(amount * Decimal(10**18)))
        try:
            gas = int(self.w3.eth.estimate_gas(tx))
        except Exception:
            gas = 21000
        tx["gas"] = max(21000, int(gas * 1.10))
        txh = self._sign_send(tx)
        self._audit("TRANSFER_NATIVE", to, self.chain.native_symbol, str(amount), str(amount), str(amount), txh, "BROADCAST")
        return {"tx_hash":txh,"amount":amount,"to":to,"explorer":f"{self.chain.explorer_url}/tx/{txh}"}

    def transfer_token(self, token: str, to_address: str, amount_spec: str, confirm: str = "CONFIRM") -> dict:
        self._confirm(confirm)
        try: to = Web3.to_checksum_address(to_address)
        except Exception as exc: raise LiveTradingError("Destination must be a valid 0x address") from exc
        token_a,c,dec,sym,raw,bal = self.token_balance(token)
        spec=str(amount_spec).strip()
        if spec.endswith("%"):
            try: pct=Decimal(spec[:-1])
            except Exception as exc: raise LiveTradingError("Token percentage is invalid") from exc
            if pct<=0 or pct>100: raise LiveTradingError("Token percentage must be >0 and <=100")
            amount_raw=int(Decimal(raw)*pct/Decimal(100))
        else:
            amount=_dec(spec,"Token transfer amount"); amount_raw=int(amount*Decimal(10**dec))
        if amount_raw<=0 or amount_raw>raw: raise LiveTradingError("Insufficient token balance")
        fn=c.functions.transfer(to,amount_raw);tx=self._base_tx();tx=self._with_gas(fn,tx);txh=self._sign_send(fn.build_transaction(tx))
        human=Decimal(amount_raw)/Decimal(10**dec)
        self._audit("TRANSFER_TOKEN",token_a,sym,str(human),str(human),str(human),txh,"BROADCAST")
        return {"tx_hash":txh,"amount":human,"symbol":sym,"to":to,"explorer":f"{self.chain.explorer_url}/tx/{txh}"}

    def transfer_wrapped_raw(self, to_address: str, amount_raw: int) -> str:
        if amount_raw <= 0: raise LiveTradingError("Fee amount must be positive")
        to=Web3.to_checksum_address(to_address);c=self.w3.eth.contract(address=self.wrapped,abi=ERC20_ABI)
        fn=c.functions.transfer(to,int(amount_raw));tx=self._base_tx();tx=self._with_gas(fn,tx);return self._sign_send(fn.build_transaction(tx))

    def tx_status(self, tx_hash: str) -> dict:
        try:
            h=Web3.to_hex(hexstr=tx_hash)
            receipt=self.w3.eth.get_transaction_receipt(h)
            return {"found":True,"status":"SUCCESS" if int(receipt.status)==1 else "FAILED","block":receipt.blockNumber,"explorer":f"{self.chain.explorer_url}/tx/{tx_hash}"}
        except Exception:
            return {"found":False,"status":"PENDING_OR_UNKNOWN","explorer":f"{self.chain.explorer_url}/tx/{tx_hash}"}

    def _audit(self, side, token, symbol, amount_in, expected_out, minimum_out, tx_hash, status, approval_hash=""):
        fields=["timestamp_epoch","telegram_id","wallet_id","chain_id","chain_slug","wallet","side","token","symbol","amount_in","expected_out","minimum_out","router","tx_hash","approval_hash","status"]
        _atomic_append(self.app.csv_dir/"auto"/"live_trade_audit.csv",{
            "timestamp_epoch":int(time.time()),"telegram_id":self.telegram_id or "LEGACY","wallet_id":self.wallet_id or "LEGACY","chain_id":self.chain.chain_id,"chain_slug":self.chain.slug,"wallet":self.address or "","side":side,"token":token,"symbol":symbol,"amount_in":amount_in,"expected_out":expected_out,"minimum_out":minimum_out,"router":self.router_address,"tx_hash":tx_hash,"approval_hash":approval_hash,"status":status,
        },fields)


def live_wallet_address(app: AppSettings) -> str | None:
    return stored_live_wallet_address(app)

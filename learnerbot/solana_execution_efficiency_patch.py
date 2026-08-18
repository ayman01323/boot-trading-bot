from __future__ import annotations

import base64
import json
import math
import sqlite3
import time
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

import requests
from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from . import solana_live_executor as _exec
from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_refundable_rent_accounting_patch as _rent
from . import solana_sibot as _sol
from . import solana_token_account_reclaim_patch as _reclaim

JUPITER_BUILD_URL = "https://api.jup.ag/swap/v2/build"
LAMPORTS_PER_SOL = Decimal(1_000_000_000)
MAX_TX_BYTES = 1232
MAX_COMPUTE_UNITS = 1_400_000
DEFAULT_BASE_FEE_LAMPORTS = 5_000

# Economic controls.  The ratio/margin caps dominate the absolute cap for small
# trades.  Rent is tracked separately because it is refundable principal rather
# than a permanent execution fee.
_EFFICIENCY_DEFAULTS = {
    "live_max_total_fee_lamports": ("100000", "Absolute ceiling for base + priority/MEV + platform-fee equivalent"),
    "live_max_fee_ratio_pct": ("3.0", "Maximum total execution fee equivalent as percent of trade value"),
    "live_expected_profit_margin_pct": ("10.0", "Expected gross edge used to constrain the fee budget"),
    "live_max_fee_share_of_expected_profit_pct": ("25", "Maximum expected gross edge allowed to be consumed by fees"),
    "live_order_slippage_bps": ("50", "Explicit LIVE swap slippage tolerance in basis points"),
    "live_max_combined_impact_slippage_bps": ("150", "Reject route if quoted price impact plus slippage exceeds this"),
    "live_multihop_max_combined_bps": ("100", "Stricter impact+slippage ceiling for multi-leg routes"),
    "live_atomic_route_deterioration_bps": ("50", "Maximum /build output deterioration versus guarded /order quote"),
    "live_enable_jito_tip": ("false", "Optional explicitly capped Jito tip on managed /order path"),
    "live_max_jito_tip_lamports": ("1000", "Hard maximum explicit Jito tip when enabled"),
    "live_require_atomic_full_close": ("true", "Require 100% exits to sell and close the proven bot-created token account atomically"),
    "live_atomic_compute_buffer_pct": ("10", "Compute-unit safety buffer above signed simulation usage"),
    "live_max_rent_exposure_lamports": ("3000000", "Reject new managed order if reported rent funding exceeds this refundable-capital ceiling"),
}
_sol.DEFAULTS.update(_EFFICIENCY_DEFAULTS)

_PREV_SWAP = _exec.SolanaLiveExecutor.swap
_PREV_SELL = _exec.SolanaLiveExecutor.sell
_PREV_RENT_CLOSE = _rent._close_live_rent_aware

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS live_execution_guard_events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  telegram_id TEXT NOT NULL,
  wallet_address TEXT,
  action TEXT NOT NULL,
  input_mint TEXT,
  output_mint TEXT,
  amount_raw TEXT,
  trade_value_lamports TEXT,
  fee_cap_lamports TEXT,
  reason TEXT NOT NULL,
  details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sol_exec_guard_user
  ON live_execution_guard_events(telegram_id,created_at DESC);

CREATE TABLE IF NOT EXISTS live_execution_receipts(
  signature TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  telegram_id TEXT NOT NULL,
  wallet_id TEXT,
  wallet_address TEXT,
  action TEXT NOT NULL,
  source TEXT NOT NULL,
  input_mint TEXT,
  output_mint TEXT,
  raw_input_amount TEXT,
  raw_output_amount TEXT,
  trade_value_lamports TEXT,
  network_fee_lamports TEXT,
  base_fee_lamports TEXT,
  priority_fee_lamports TEXT,
  jito_tip_lamports TEXT,
  priority_tip_quote_lamports TEXT,
  platform_fee_bps TEXT,
  platform_fee_amount TEXT,
  platform_fee_mint TEXT,
  rent_paid_lamports TEXT,
  rent_reclaimed_lamports TEXT,
  wallet_delta_lamports TEXT,
  net_execution_pnl_lamports TEXT,
  slippage_bps TEXT,
  price_impact_bps TEXT,
  route_hops INTEGER,
  atomic_token_account_close INTEGER NOT NULL DEFAULT 0,
  reused_existing_output_token_account INTEGER NOT NULL DEFAULT 0,
  router TEXT,
  details_json TEXT,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sol_exec_receipts_user
  ON live_execution_receipts(telegram_id,created_at DESC);
"""


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _i(value, default=0) -> int:
    try:
        return int(Decimal(str(value)))
    except Exception:
        return int(default)


def _bool(value, default=False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _cfg(app) -> dict:
    return dict(_sol.settings(app))


def _ensure(conn):
    conn.executescript(_SCHEMA)


def _action(input_mint: str, output_mint: str) -> str:
    if str(input_mint) == str(_sol.WSOL_MINT):
        return "BUY"
    if str(output_mint) == str(_sol.WSOL_MINT):
        return "SELL"
    return "SWAP"


def dynamic_fee_cap_lamports(cfg: dict, trade_value_lamports: int) -> int:
    """Hard total-fee ceiling from absolute, trade-ratio and expected-edge caps."""
    trade_value = max(0, int(trade_value_lamports))
    absolute = max(DEFAULT_BASE_FEE_LAMPORTS, _i(cfg.get("live_max_total_fee_lamports"), 100_000))
    ratio = max(Decimal(0), _d(cfg.get("live_max_fee_ratio_pct"), "3")) / Decimal(100)
    expected = max(Decimal(0), _d(cfg.get("live_expected_profit_margin_pct"), "10")) / Decimal(100)
    share = max(Decimal(0), _d(cfg.get("live_max_fee_share_of_expected_profit_pct"), "25")) / Decimal(100)
    ratio_cap = int((Decimal(trade_value) * ratio).to_integral_value(rounding=ROUND_FLOOR))
    edge_cap = int((Decimal(trade_value) * expected * share).to_integral_value(rounding=ROUND_FLOOR))
    candidates = [absolute]
    if ratio_cap > 0:
        candidates.append(ratio_cap)
    if edge_cap > 0:
        candidates.append(edge_cap)
    return max(DEFAULT_BASE_FEE_LAMPORTS, min(candidates))


def _platform_fee_equivalent(order: dict, trade_value_lamports: int) -> int:
    platform = dict(order.get("platformFee") or {})
    bps = _d(platform.get("feeBps") if platform else order.get("feeBps"), 0)
    if bps <= 0:
        return 0
    return max(0, int((Decimal(max(0, int(trade_value_lamports))) * bps / Decimal(10_000)).to_integral_value(rounding=ROUND_FLOOR)))


def _price_impact_bps(order: dict) -> Decimal:
    # Swap V2 priceImpact is expressed in percentage points: 0.1 == 0.1%.
    if order.get("priceImpact") is not None:
        return abs(_d(order.get("priceImpact"), 0)) * Decimal(100)
    # Legacy priceImpactPct is a decimal fraction: 0.001 == 0.1%.
    if order.get("priceImpactPct") is not None:
        return abs(_d(order.get("priceImpactPct"), 0)) * Decimal(10_000)
    return Decimal(0)


def _route_hops(order: dict) -> int:
    route = list(order.get("routePlan") or [])
    return max(1, len(route)) if route else 1


def _guard_event(executor, *, action: str, input_mint: str, output_mint: str, amount_raw: int,
                 trade_value_lamports: int, fee_cap_lamports: int, reason: str, details: dict | None = None):
    now = int(time.time())
    try:
        with _sol._DB_LOCK, closing(_sol.connect(executor.app)) as conn:
            _ensure(conn)
            conn.execute(
                """INSERT INTO live_execution_guard_events(
                     created_at,telegram_id,wallet_address,action,input_mint,output_mint,amount_raw,
                     trade_value_lamports,fee_cap_lamports,reason,details_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (now, str(executor.telegram_id), str(getattr(executor, "address", "")), str(action),
                 str(input_mint), str(output_mint), str(amount_raw), str(trade_value_lamports),
                 str(fee_cap_lamports), str(reason)[:900], json.dumps(details or {}, separators=(",", ":"), default=str)[:4000]),
            )
            conn.commit()
    except Exception:
        pass
    print("[solana-exec-efficiency] REJECT tid=%s action=%s reason=%s" % (executor.telegram_id, action, str(reason)[:300]))


def _reject(executor, reason: str, *, input_mint: str, output_mint: str, amount_raw: int,
            trade_value_lamports: int = 0, fee_cap_lamports: int = 0, details: dict | None = None):
    _guard_event(
        executor,
        action=_action(input_mint, output_mint),
        input_mint=input_mint,
        output_mint=output_mint,
        amount_raw=amount_raw,
        trade_value_lamports=trade_value_lamports,
        fee_cap_lamports=fee_cap_lamports,
        reason=reason,
        details=details,
    )
    raise _exec.SolanaLiveError("Economic execution guard: " + str(reason))


def _headers(executor):
    return executor._headers(False)


def _request_quote_only(executor, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict:
    params = {
        "inputMint": str(input_mint),
        "outputMint": str(output_mint),
        "amount": str(int(amount_raw)),
        "slippageBps": str(int(slippage_bps)),
        "excludeRouters": "jupiterz",
    }
    r = requests.get(f"{_exec.JUPITER_BASE}/order", params=params, headers=_headers(executor), timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errorCode") or _i(data.get("outAmount") or data.get("outputAmount"), 0) <= 0:
        raise _exec.SolanaLiveError(
            "Jupiter quote failed: " + str(data.get("errorMessage") or data.get("errorCode") or "no positive output")
        )
    return data


def _trade_value_for_order(input_mint: str, output_mint: str, amount_raw: int, quote: dict | None = None) -> int:
    if str(input_mint) == str(_sol.WSOL_MINT):
        return max(0, int(amount_raw))
    if str(output_mint) == str(_sol.WSOL_MINT):
        return max(0, _i((quote or {}).get("outAmount") or (quote or {}).get("outputAmount"), 0))
    return 0


def _validate_order(executor, order: dict, input_mint: str, output_mint: str, amount_raw: int,
                    trade_value_lamports: int, fee_cap_lamports: int, cfg: dict) -> dict:
    if order.get("errorCode") or not order.get("transaction"):
        _reject(
            executor,
            str(order.get("errorMessage") or order.get("errorCode") or "Jupiter returned no transaction"),
            input_mint=input_mint, output_mint=output_mint, amount_raw=amount_raw,
            trade_value_lamports=trade_value_lamports, fee_cap_lamports=fee_cap_lamports,
        )
    if not order.get("requestId"):
        _reject(executor, "Jupiter order returned no requestId", input_mint=input_mint, output_mint=output_mint,
                amount_raw=amount_raw, trade_value_lamports=trade_value_lamports, fee_cap_lamports=fee_cap_lamports)
    if str(order.get("router") or "").lower() == "jupiterz":
        _reject(executor, "JupiterZ/RFQ is not allowed on the single-signer LIVE path", input_mint=input_mint,
                output_mint=output_mint, amount_raw=amount_raw, trade_value_lamports=trade_value_lamports,
                fee_cap_lamports=fee_cap_lamports)

    slippage = Decimal(max(0, _i(order.get("slippageBps"), _i(cfg.get("live_order_slippage_bps"), 50))))
    impact = _price_impact_bps(order)
    hops = _route_hops(order)
    combined = slippage + impact
    max_combined = max(Decimal(1), _d(cfg.get("live_max_combined_impact_slippage_bps"), 150))
    if hops > 1:
        max_combined = min(max_combined, max(Decimal(1), _d(cfg.get("live_multihop_max_combined_bps"), 100)))
    if combined > max_combined:
        _reject(
            executor,
            f"quoted price impact {impact:.2f} bps + slippage {slippage:.0f} bps = {combined:.2f} bps exceeds {max_combined:.0f} bps",
            input_mint=input_mint, output_mint=output_mint, amount_raw=amount_raw,
            trade_value_lamports=trade_value_lamports, fee_cap_lamports=fee_cap_lamports,
            details={"price_impact_bps": str(impact), "slippage_bps": str(slippage), "route_hops": hops},
        )

    base_fee = max(0, _i(order.get("signatureFeeLamports"), DEFAULT_BASE_FEE_LAMPORTS))
    priority_tip = max(0, _i(order.get("prioritizationFeeLamports"), 0))
    platform_equiv = _platform_fee_equivalent(order, trade_value_lamports)
    total_fee_equiv = base_fee + priority_tip + platform_equiv
    if total_fee_equiv > int(fee_cap_lamports):
        _reject(
            executor,
            f"total fee equivalent {total_fee_equiv} lamports exceeds dynamic cap {fee_cap_lamports}",
            input_mint=input_mint, output_mint=output_mint, amount_raw=amount_raw,
            trade_value_lamports=trade_value_lamports, fee_cap_lamports=fee_cap_lamports,
            details={
                "base_fee_lamports": base_fee,
                "priority_tip_lamports": priority_tip,
                "platform_fee_equiv_lamports": platform_equiv,
            },
        )

    rent = max(0, _i(order.get("rentFeeLamports"), 0))
    max_rent = max(0, _i(cfg.get("live_max_rent_exposure_lamports"), 3_000_000))
    if max_rent and rent > max_rent:
        _reject(
            executor,
            f"reported refundable rent funding {rent} lamports exceeds exposure cap {max_rent}",
            input_mint=input_mint, output_mint=output_mint, amount_raw=amount_raw,
            trade_value_lamports=trade_value_lamports, fee_cap_lamports=fee_cap_lamports,
        )

    order = dict(order)
    order["_fee_cap_lamports"] = int(fee_cap_lamports)
    order["_trade_value_lamports"] = int(trade_value_lamports)
    order["_platform_fee_equiv_lamports"] = int(platform_equiv)
    order["_price_impact_bps"] = str(impact)
    order["_route_hops"] = int(hops)
    order["_total_fee_equiv_lamports"] = int(total_fee_equiv)
    return order


def order_with_economic_caps(self, input_mint: str, output_mint: str, amount_raw: int) -> dict:
    if int(amount_raw) <= 0:
        raise _exec.SolanaLiveError("Swap amount must be positive")
    cfg = _cfg(self.app)
    slippage_bps = max(1, min(10_000, _i(cfg.get("live_order_slippage_bps"), 50)))

    quote = None
    if str(output_mint) == str(_sol.WSOL_MINT) and str(input_mint) != str(_sol.WSOL_MINT):
        quote = _request_quote_only(self, input_mint, output_mint, amount_raw, slippage_bps)
    trade_value = _trade_value_for_order(input_mint, output_mint, amount_raw, quote)
    if trade_value <= 0:
        raise _exec.SolanaLiveError("Cannot establish SOL trade value for economic fee cap")
    fee_cap = dynamic_fee_cap_lamports(cfg, trade_value)

    platform_hint = _platform_fee_equivalent(quote or {}, trade_value)
    explicit_jito = 0
    if _bool(cfg.get("live_enable_jito_tip"), False):
        explicit_jito = min(
            max(0, _i(cfg.get("live_max_jito_tip_lamports"), 1000)),
            max(0, fee_cap - DEFAULT_BASE_FEE_LAMPORTS - platform_hint),
        )
        if explicit_jito < 1000:
            explicit_jito = 0
    priority_cap = max(0, fee_cap - DEFAULT_BASE_FEE_LAMPORTS - platform_hint - explicit_jito)

    params = {
        "inputMint": str(input_mint),
        "outputMint": str(output_mint),
        "amount": str(int(amount_raw)),
        "taker": self.address,
        "excludeRouters": "jupiterz",
        "slippageBps": str(slippage_bps),
        "broadcastFeeType": "maxCap",
    }
    if priority_cap > 0:
        params["priorityFeeLamports"] = str(int(priority_cap))
    if explicit_jito >= 1000:
        params["jitoTipLamports"] = str(int(explicit_jito))

    r = requests.get(f"{_exec.JUPITER_BASE}/order", params=params, headers=_headers(self), timeout=30)
    r.raise_for_status()
    order = r.json()
    order = _validate_order(self, order, input_mint, output_mint, amount_raw, trade_value, fee_cap, cfg)
    order["_requested_priority_fee_cap_lamports"] = int(priority_cap)
    order["_requested_jito_tip_lamports"] = int(explicit_jito)
    return order


def _confirmed_transaction(app, signature: str, attempts=6):
    for _ in range(max(1, int(attempts))):
        try:
            tx = _sol._rpc(
                app,
                "getTransaction",
                [str(signature), {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
            )
            if tx:
                return tx
        except Exception:
            pass
        time.sleep(0.35)
    return None


def _token_account_count(executor, mint: str) -> int:
    try:
        rows = _reclaim._token_accounts(executor, mint)
        return len(rows or {}) if rows is not None else 0
    except Exception:
        return 0


def _persist_receipt(executor, result: dict, input_mint: str, output_mint: str, amount_raw: int,
                     *, source: str, atomic_close: bool = False, reused_output_account: bool = False,
                     net_execution_pnl_lamports: int | None = None):
    result = dict(result or {})
    signature = str(result.get("signature") or "")
    if not signature:
        return None
    order = dict(result.get("order") or {})
    tx = _confirmed_transaction(executor.app, signature)
    meta = dict((tx or {}).get("meta") or {})
    network_fee = max(0, _i(meta.get("fee"), 0))
    base_fee = max(0, _i(order.get("signatureFeeLamports"), DEFAULT_BASE_FEE_LAMPORTS))
    if network_fee <= 0:
        network_fee = base_fee + max(0, _i(order.get("prioritizationFeeLamports"), 0))
    priority_fee = max(0, network_fee - base_fee)
    quoted_priority_tip = max(0, _i(order.get("prioritizationFeeLamports"), priority_fee))
    jito_tip = max(0, _i(order.get("_requested_jito_tip_lamports"), result.get("jito_tip_lamports") or 0))
    platform = dict(order.get("platformFee") or {})
    fee_bps = platform.get("feeBps") if platform else order.get("feeBps")
    fee_amount = platform.get("amount") if platform else ""
    fee_mint = platform.get("feeMint") or order.get("feeMint") or ""
    trade_value = max(0, _i(order.get("_trade_value_lamports"), 0))
    if trade_value <= 0:
        if str(input_mint) == str(_sol.WSOL_MINT):
            trade_value = int(amount_raw)
        elif str(output_mint) == str(_sol.WSOL_MINT):
            trade_value = _i(result.get("totalOutputAmount") or result.get("outputAmountResult"), 0)
    price_impact = _d(order.get("_price_impact_bps"), _price_impact_bps(order))
    hops = max(1, _i(order.get("_route_hops"), _route_hops(order)))
    raw_in = _i(result.get("totalInputAmount") or result.get("inputAmountResult"), amount_raw)
    raw_out = _i(result.get("totalOutputAmount") or result.get("outputAmountResult"), 0)
    rent_paid = max(0, _i(order.get("rentFeeLamports"), result.get("rent_paid_lamports") or 0))
    rent_reclaimed = max(0, _i(result.get("rent_reclaimed_lamports"), 0))
    wallet_delta = result.get("wallet_delta_lamports")
    wallet_delta_text = "" if wallet_delta is None else str(_i(wallet_delta, 0))
    now = int(time.time())

    details = {
        "fee_cap_lamports": order.get("_fee_cap_lamports"),
        "total_fee_equiv_lamports": order.get("_total_fee_equiv_lamports"),
        "wallet_balance_reconciled": result.get("wallet_balance_reconciled"),
        "swap_events_present": result.get("swap_events_present"),
        "economic_validation": result.get("economic_validation"),
        "atomic_source_account": result.get("atomic_source_account"),
    }
    try:
        with _sol._DB_LOCK, closing(_sol.connect(executor.app)) as conn:
            _ensure(conn)
            conn.execute(
                """INSERT INTO live_execution_receipts(
                     signature,created_at,telegram_id,wallet_id,wallet_address,action,source,input_mint,output_mint,
                     raw_input_amount,raw_output_amount,trade_value_lamports,network_fee_lamports,base_fee_lamports,
                     priority_fee_lamports,jito_tip_lamports,priority_tip_quote_lamports,platform_fee_bps,
                     platform_fee_amount,platform_fee_mint,rent_paid_lamports,rent_reclaimed_lamports,
                     wallet_delta_lamports,net_execution_pnl_lamports,slippage_bps,price_impact_bps,route_hops,
                     atomic_token_account_close,reused_existing_output_token_account,router,details_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(signature) DO UPDATE SET
                     raw_input_amount=excluded.raw_input_amount,raw_output_amount=excluded.raw_output_amount,
                     network_fee_lamports=excluded.network_fee_lamports,base_fee_lamports=excluded.base_fee_lamports,
                     priority_fee_lamports=excluded.priority_fee_lamports,jito_tip_lamports=excluded.jito_tip_lamports,
                     priority_tip_quote_lamports=excluded.priority_tip_quote_lamports,
                     platform_fee_bps=excluded.platform_fee_bps,platform_fee_amount=excluded.platform_fee_amount,
                     platform_fee_mint=excluded.platform_fee_mint,rent_paid_lamports=excluded.rent_paid_lamports,
                     rent_reclaimed_lamports=excluded.rent_reclaimed_lamports,wallet_delta_lamports=excluded.wallet_delta_lamports,
                     net_execution_pnl_lamports=COALESCE(excluded.net_execution_pnl_lamports,live_execution_receipts.net_execution_pnl_lamports),
                     atomic_token_account_close=excluded.atomic_token_account_close,
                     reused_existing_output_token_account=excluded.reused_existing_output_token_account,
                     details_json=excluded.details_json,updated_at=excluded.updated_at""",
                (
                    signature, now, str(executor.telegram_id), str(getattr(executor, "wallet_id", "")),
                    str(executor.address), _action(input_mint, output_mint), str(source), str(input_mint), str(output_mint),
                    str(raw_in), str(raw_out), str(trade_value), str(network_fee), str(base_fee), str(priority_fee),
                    str(jito_tip), str(quoted_priority_tip), str(fee_bps or ""), str(fee_amount or ""), str(fee_mint),
                    str(rent_paid), str(rent_reclaimed), wallet_delta_text,
                    None if net_execution_pnl_lamports is None else str(int(net_execution_pnl_lamports)),
                    str(_i(order.get("slippageBps"), 0)), str(price_impact), int(hops),
                    1 if atomic_close else 0, 1 if reused_output_account else 0, str(order.get("router") or source),
                    json.dumps(details, separators=(",", ":"), default=str), now,
                ),
            )
            conn.commit()
    except Exception as exc:
        print("[solana-exec-receipt]", type(exc).__name__, str(exc)[:300])
        return None
    return signature


def _receipt_row(app, signature: str):
    try:
        with closing(_sol.connect(app)) as conn:
            _ensure(conn)
            row = conn.execute("SELECT * FROM live_execution_receipts WHERE signature=?", (str(signature),)).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _update_receipt_pnl(app, signature: str, net_sol) -> None:
    if not signature:
        return
    lamports = int((_d(net_sol, 0) * LAMPORTS_PER_SOL).to_integral_value())
    try:
        with _sol._DB_LOCK, closing(_sol.connect(app)) as conn:
            _ensure(conn)
            conn.execute(
                "UPDATE live_execution_receipts SET net_execution_pnl_lamports=?,updated_at=? WHERE signature=?",
                (str(lamports), int(time.time()), str(signature)),
            )
            conn.commit()
    except Exception:
        pass


def _notify_receipt(app, tid: str, signature: str):
    row = _receipt_row(app, signature)
    if not row:
        return
    def sol(key):
        return _d(row.get(key), 0) / LAMPORTS_PER_SOL
    pnl_raw = row.get("net_execution_pnl_lamports")
    pnl_line = "Net execution P&L: <b>pending position close</b>" if pnl_raw in (None, "") else f"Net execution P&L: <b>{sol('net_execution_pnl_lamports'):+.9f} SOL</b>"
    platform = str(row.get("platform_fee_amount") or "0")
    platform_mint = str(row.get("platform_fee_mint") or "")
    _live._notify(
        app,
        tid,
        "🧾 <b>Solana execution receipt</b>\n"
        f"Action: <b>{row.get('action')}</b> • {'ATOMIC CLOSE' if int(row.get('atomic_token_account_close') or 0) else row.get('source')}\n"
        f"Raw swap: <code>{row.get('raw_input_amount')}</code> → <code>{row.get('raw_output_amount')}</code>\n"
        f"Network fee: <b>{sol('network_fee_lamports'):.9f} SOL</b>\n"
        f"• Base: {sol('base_fee_lamports'):.9f} SOL\n"
        f"• Priority: {sol('priority_fee_lamports'):.9f} SOL\n"
        f"• Explicit Jito tip: {sol('jito_tip_lamports'):.9f} SOL\n"
        f"Router/platform fee: <code>{platform}</code>{(' ' + platform_mint[:8] + '…') if platform_mint else ''}\n"
        f"Rent paid: {sol('rent_paid_lamports'):.9f} SOL • reclaimed: {sol('rent_reclaimed_lamports'):.9f} SOL\n"
        f"Impact/slippage: {row.get('price_impact_bps')} / {row.get('slippage_bps')} bps\n"
        f"{pnl_line}\n"
        f"TX: <code>{signature}</code>",
    )


def swap_with_cost_receipt(self, input_mint: str, output_mint: str, amount_raw: int):
    reuse_available = False
    if str(input_mint) == str(_sol.WSOL_MINT) and str(output_mint) != str(_sol.WSOL_MINT):
        reuse_available = _token_account_count(self, output_mint) > 0
    try:
        result = dict(_PREV_SWAP(self, input_mint, output_mint, amount_raw) or {})
    except _exec.SolanaLivePostExecutionError as exc:
        result = dict(getattr(exc, "result", {}) or {})
        if result.get("signature"):
            _persist_receipt(
                self, result, input_mint, output_mint, amount_raw,
                source="JUPITER_ORDER_LANDED_INVALID", reused_output_account=reuse_available,
            )
        raise
    _persist_receipt(
        self, result, input_mint, output_mint, amount_raw,
        source="JUPITER_ORDER", reused_output_account=reuse_available,
    )
    if _action(input_mint, output_mint) == "BUY" and result.get("signature"):
        _notify_receipt(self.app, self.telegram_id, str(result.get("signature")))
    return result


def _api_instruction(raw: dict) -> Instruction:
    if not isinstance(raw, dict) or not raw.get("programId"):
        raise _exec.SolanaLiveError("Jupiter /build returned an invalid instruction")
    try:
        data = base64.b64decode(str(raw.get("data") or ""), validate=True)
        accounts = [
            AccountMeta(
                Pubkey.from_string(str(a.get("pubkey"))),
                bool(a.get("isSigner")),
                bool(a.get("isWritable")),
            )
            for a in (raw.get("accounts") or [])
        ]
        return Instruction(Pubkey.from_string(str(raw["programId"])), data, accounts)
    except Exception as exc:
        raise _exec.SolanaLiveError("Cannot decode Jupiter /build instruction") from exc


def _lookup_tables(build: dict):
    out = []
    raw = build.get("addressesByLookupTableAddress") or {}
    if not isinstance(raw, dict):
        return out
    for key, addresses in raw.items():
        out.append(
            AddressLookupTableAccount(
                key=Pubkey.from_string(str(key)),
                addresses=[Pubkey.from_string(str(a)) for a in (addresses or [])],
            )
        )
    return out


def _atomic_candidate(executor, mint: str, amount_raw: int):
    actual = int(executor.token_balance_raw(mint))
    if int(amount_raw) < actual:
        return None
    if actual <= 0 or int(amount_raw) != actual:
        raise _exec.SolanaLiveError(f"Atomic full exit requires exact whole-token balance: have {actual}, requested {amount_raw}")

    with closing(_sol.connect(executor.app)) as conn:
        positions = [dict(r) for r in conn.execute(
            """SELECT * FROM positions WHERE telegram_id=? AND mint=? AND mode='LIVE' AND status='OPEN' ORDER BY entry_ts""",
            (str(executor.telegram_id), str(mint)),
        ).fetchall()]
    if len(positions) != 1:
        raise _exec.SolanaLiveError(f"Atomic full exit requires exactly one OPEN LIVE position for mint; found {len(positions)}")
    position = positions[0]
    binding = _binding._binding(executor.app, position.get("position_id"))
    if not binding or str(binding.get("wallet_address") or "") != str(executor.address):
        raise _exec.SolanaLiveError("Atomic full exit wallet does not match the recorded entry-wallet binding")

    current = _reclaim._token_accounts(executor, mint)
    if current is None:
        raise _exec.SolanaLiveError("Atomic full exit cannot prove token-account state")
    positive = [row for row in current.values() if int(row.get("amount") or 0) > 0]
    if len(positive) != 1 or int(positive[0].get("amount") or 0) != actual:
        raise _exec.SolanaLiveError("Atomic full exit requires one unambiguous token account holding the entire position")
    live_account = positive[0]

    tracked = _reclaim._tracked_accounts(executor.app, position.get("position_id"))
    matches = [r for r in tracked if str(r.get("account_pubkey") or "") == str(live_account.get("pubkey") or "")]
    if len(matches) != 1:
        raise _exec.SolanaLiveError("Atomic full exit refuses to close an account not proven to have been created by this bot")
    row = matches[0]
    if str(row.get("program_id") or "") not in _reclaim._ALLOWED_TOKEN_PROGRAMS:
        raise _exec.SolanaLiveError("Atomic full exit token program is not approved")
    if str(row.get("program_id") or "") != str(live_account.get("program_id") or ""):
        raise _exec.SolanaLiveError("Atomic full exit token-program mismatch")
    return position, row, live_account


def _build_atomic_swap(executor, input_mint: str, amount_raw: int, slippage_bps: int) -> dict:
    params = {
        "inputMint": str(input_mint),
        "outputMint": str(_sol.WSOL_MINT),
        "amount": str(int(amount_raw)),
        "taker": str(executor.address),
        "slippageBps": str(int(slippage_bps)),
        "wrapAndUnwrapSol": "true",
        "nativeDestinationAccount": str(executor.address),
        "maxAccounts": "64",
    }
    r = requests.get(JUPITER_BUILD_URL, params=params, headers=_headers(executor), timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("error") or data.get("errorCode") or _i(data.get("outAmount"), 0) <= 0 or not data.get("swapInstruction"):
        raise _exec.SolanaLiveError(
            "Jupiter /build failed: " + str(data.get("error") or data.get("errorMessage") or data.get("errorCode") or "invalid build")
        )
    return data


def _compile_tx(owner: Pubkey, instructions: list[Instruction], lookups, blockhash: Hash, signer: Keypair):
    message = MessageV0.try_compile(owner, instructions, lookups, blockhash)
    return VersionedTransaction(message, [signer])


def atomic_full_sell(self, input_mint: str, amount_raw: int, candidate) -> dict:
    position, tracked_row, live_account = candidate
    cfg = _cfg(self.app)
    slippage_bps = max(1, min(10_000, _i(cfg.get("live_order_slippage_bps"), 50)))

    # Guarded /order is used as a no-broadcast market/fee/congestion probe.  We
    # deliberately discard its prebuilt transaction because it cannot be modified.
    probe = self._order(input_mint, _sol.WSOL_MINT, int(amount_raw))
    fee_cap = max(DEFAULT_BASE_FEE_LAMPORTS, _i(probe.get("_fee_cap_lamports"), 0))
    trade_value = max(1, _i(probe.get("_trade_value_lamports"), probe.get("outAmount") or 0))
    required_priority = max(0, _i(probe.get("prioritizationFeeLamports"), 0))

    build = _build_atomic_swap(self, input_mint, amount_raw, slippage_bps)
    build_out = max(1, _i(build.get("outAmount"), 0))
    probe_out = max(1, _i(probe.get("outAmount") or probe.get("outputAmount"), build_out))
    deterioration = max(Decimal(0), (Decimal(probe_out - build_out) * Decimal(10_000) / Decimal(probe_out)))
    max_deterioration = max(Decimal(0), _d(cfg.get("live_atomic_route_deterioration_bps"), 50))
    if deterioration > max_deterioration:
        _reject(
            self,
            f"atomic /build route is {deterioration:.2f} bps worse than guarded /order quote (limit {max_deterioration:.0f})",
            input_mint=input_mint, output_mint=_sol.WSOL_MINT, amount_raw=amount_raw,
            trade_value_lamports=trade_value, fee_cap_lamports=fee_cap,
        )
    build_hops = _route_hops(build)
    if build_hops > 1:
        combined = deterioration + Decimal(slippage_bps)
        multi_max = max(Decimal(1), _d(cfg.get("live_multihop_max_combined_bps"), 100))
        if combined > multi_max:
            _reject(
                self,
                f"multi-leg atomic route deterioration+slippage {combined:.2f} bps exceeds {multi_max:.0f} bps",
                input_mint=input_mint, output_mint=_sol.WSOL_MINT, amount_raw=amount_raw,
                trade_value_lamports=trade_value, fee_cap_lamports=fee_cap,
            )

    owner = Keypair.from_bytes(bytes(self.keypair))
    if str(owner.pubkey()) != str(self.address):
        raise _exec.SolanaLiveError("Atomic full-exit signer does not match execution wallet")
    owner_pk = owner.pubkey()

    close_ix = Instruction(
        Pubkey.from_string(str(live_account["program_id"])),
        bytes([9]),
        [
            AccountMeta(Pubkey.from_string(str(live_account["pubkey"])), False, True),
            AccountMeta(owner_pk, False, True),
            AccountMeta(owner_pk, True, False),
        ],
    )
    core = []
    core.extend(_api_instruction(ix) for ix in (build.get("setupInstructions") or []))
    core.append(_api_instruction(build["swapInstruction"]))
    core.append(close_ix)
    if build.get("cleanupInstruction"):
        core.append(_api_instruction(build["cleanupInstruction"]))
    core.extend(_api_instruction(ix) for ix in (build.get("otherInstructions") or []))
    lookups = _lookup_tables(build)

    latest = _sol._rpc(self.app, "getLatestBlockhash", [{"commitment": "confirmed"}]) or {}
    blockhash_text = str((latest.get("value") or {}).get("blockhash") or "")
    if not blockhash_text:
        raise _exec.SolanaLiveError("Atomic full exit cannot obtain a recent blockhash")
    blockhash = Hash.from_string(blockhash_text)

    sim_tx = _compile_tx(owner_pk, [set_compute_unit_limit(MAX_COMPUTE_UNITS), *core], lookups, blockhash, owner)
    sim_b64 = base64.b64encode(bytes(sim_tx)).decode("ascii")
    sim = _sol._rpc(
        self.app,
        "simulateTransaction",
        [sim_b64, {"encoding": "base64", "sigVerify": True, "commitment": "confirmed"}],
    ) or {}
    value = sim.get("value") or {}
    if value.get("err") is not None:
        logs = value.get("logs") or []
        raise _exec.SolanaLiveError(
            "Atomic sell+CloseAccount simulation failed: " + str(value.get("err")) + " | " + " | ".join(map(str, logs[-5:]))[:600]
        )
    consumed = max(1, _i(value.get("unitsConsumed"), 0))
    buffer_pct = max(0, min(50, _i(cfg.get("live_atomic_compute_buffer_pct"), 10)))
    cu_limit = min(MAX_COMPUTE_UNITS, max(50_000, int(math.ceil(consumed * (100 + buffer_pct) / 100))))

    priority_budget = max(0, fee_cap - DEFAULT_BASE_FEE_LAMPORTS)
    if required_priority > priority_budget:
        _reject(
            self,
            f"current managed-route priority/tip estimate {required_priority} exceeds atomic fee budget {priority_budget}",
            input_mint=input_mint, output_mint=_sol.WSOL_MINT, amount_raw=amount_raw,
            trade_value_lamports=trade_value, fee_cap_lamports=fee_cap,
        )
    target_priority = min(priority_budget, required_priority)
    cu_price = 0 if target_priority <= 0 else max(1, (target_priority * 1_000_000) // cu_limit)
    projected_priority = int(math.ceil(cu_price * cu_limit / 1_000_000))
    if DEFAULT_BASE_FEE_LAMPORTS + projected_priority > fee_cap:
        _reject(
            self,
            f"projected atomic network fee {DEFAULT_BASE_FEE_LAMPORTS + projected_priority} exceeds cap {fee_cap}",
            input_mint=input_mint, output_mint=_sol.WSOL_MINT, amount_raw=amount_raw,
            trade_value_lamports=trade_value, fee_cap_lamports=fee_cap,
        )

    final_instructions = [set_compute_unit_limit(cu_limit), set_compute_unit_price(cu_price), *core]
    tx = _compile_tx(owner_pk, final_instructions, lookups, blockhash, owner)
    raw = bytes(tx)
    if len(raw) > MAX_TX_BYTES:
        raise _exec.SolanaLiveError(f"Atomic sell+close transaction is {len(raw)} bytes, above Solana limit {MAX_TX_BYTES}")

    wallet_before = int(self.native_balance_lamports())
    token_before = int(self.token_balance_raw(input_mint))
    signed_b64 = base64.b64encode(raw).decode("ascii")
    signature = str(_sol._rpc(
        self.app,
        "sendTransaction",
        [signed_b64, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed", "maxRetries": 3}],
    ) or "")
    if not signature:
        raise _exec.SolanaLiveError("Atomic sell+close RPC returned no signature")

    tx_json = _confirmed_transaction(self.app, signature, attempts=12)
    if not tx_json:
        raise _exec.SolanaLivePostExecutionError(
            "Atomic sell+close was submitted but confirmation details are unavailable",
            {"signature": signature, "wallet_before_lamports": wallet_before},
        )
    meta = dict(tx_json.get("meta") or {})
    if meta.get("err") is not None:
        raise _exec.SolanaLivePostExecutionError(
            "Atomic sell+close landed with transaction error: " + str(meta.get("err")),
            {"signature": signature, "wallet_before_lamports": wallet_before},
        )

    wallet_after = int(self.native_balance_lamports())
    token_after = int(self.token_balance_raw(input_mint))
    if token_before - token_after <= 0:
        raise _exec.SolanaLivePostExecutionError(
            "Atomic sell+close landed but input-token balance did not decrease",
            {"signature": signature, "wallet_before_lamports": wallet_before, "wallet_after_lamports": wallet_after},
        )
    current_after = _reclaim._token_accounts(self, input_mint)
    if current_after is None or str(live_account["pubkey"]) in current_after:
        raise _exec.SolanaLivePostExecutionError(
            "Atomic sell landed but the proven bot-created token account was not closed",
            {"signature": signature, "wallet_before_lamports": wallet_before, "wallet_after_lamports": wallet_after},
        )

    rent_reclaimed = max(0, int(live_account.get("lamports") or tracked_row.get("entry_lamports") or 0))
    try:
        _reclaim._mark_reconciled(
            self.app, str(position.get("position_id") or ""), signature, [tracked_row], rent_reclaimed
        )
    except Exception as exc:
        print("[solana-atomic-close-reconcile]", type(exc).__name__, str(exc)[:300])

    network_fee = max(0, _i(meta.get("fee"), DEFAULT_BASE_FEE_LAMPORTS + projected_priority))
    wallet_delta = wallet_after - wallet_before
    swap_received = max(1, wallet_delta + network_fee - rent_reclaimed)
    order_for_receipt = dict(probe)
    order_for_receipt["router"] = "metis-build-atomic"
    order_for_receipt["platformFee"] = {"amount": "0", "feeBps": 0, "feeMint": ""}
    order_for_receipt["feeBps"] = 0
    order_for_receipt["prioritizationFeeLamports"] = projected_priority
    order_for_receipt["signatureFeeLamports"] = DEFAULT_BASE_FEE_LAMPORTS
    order_for_receipt["rentFeeLamports"] = 0
    order_for_receipt["slippageBps"] = slippage_bps
    order_for_receipt["_route_hops"] = build_hops
    order_for_receipt["_trade_value_lamports"] = trade_value
    order_for_receipt["_total_fee_equiv_lamports"] = network_fee
    order_for_receipt["_requested_jito_tip_lamports"] = 0

    result = {
        "status": "Success",
        "code": 0,
        "signature": signature,
        "totalInputAmount": str(int(amount_raw)),
        "totalOutputAmount": str(int(swap_received)),
        "inputAmountResult": str(int(amount_raw)),
        "outputAmountResult": str(int(swap_received)),
        "wallet_before_lamports": wallet_before,
        "wallet_after_lamports": wallet_after,
        "wallet_delta_lamports": wallet_delta,
        "wallet_balance_reconciled": True,
        "input_token_before_raw": token_before,
        "input_token_after_raw": token_after,
        "input_token_delta_raw": token_before - token_after,
        "rent_reclaimed_lamports": rent_reclaimed,
        "atomic_token_account_close": True,
        "atomic_source_account": str(live_account["pubkey"]),
        "order": order_for_receipt,
        "simulation": value,
    }
    _persist_receipt(
        self, result, input_mint, _sol.WSOL_MINT, amount_raw,
        source="JUPITER_BUILD_RPC", atomic_close=True,
    )
    return result


def sell_with_atomic_account_close(self, input_mint: str, amount_raw: int):
    actual = int(self.token_balance_raw(input_mint))
    if int(amount_raw) < actual:
        return _PREV_SELL(self, input_mint, amount_raw)
    cfg = _cfg(self.app)
    if not _bool(cfg.get("live_require_atomic_full_close"), True):
        return _PREV_SELL(self, input_mint, amount_raw)
    candidate = _atomic_candidate(self, input_mint, amount_raw)
    if candidate is None:
        return _PREV_SELL(self, input_mint, amount_raw)
    return atomic_full_sell(self, input_mint, amount_raw, candidate)


def close_live_with_receipt_pnl(app, tid, position, fraction: Decimal, reason: str):
    result = _PREV_RENT_CLOSE(app, tid, position, fraction, reason)
    signature = str((result or {}).get("signature") or "")
    if signature:
        _update_receipt_pnl(app, signature, (result or {}).get("net_sol") or 0)
        _notify_receipt(app, str(tid), signature)
    return result


def execution_efficiency_stack_intact() -> bool:
    return bool(
        getattr(_exec.SolanaLiveExecutor, "_execution_efficiency_patch", False)
        and _exec.SolanaLiveExecutor._order is order_with_economic_caps
        and _rent._close_live_rent_aware is close_live_with_receipt_pnl
    )


def install():
    if getattr(_exec.SolanaLiveExecutor, "_execution_efficiency_patch", False):
        return
    _exec.SolanaLiveExecutor._order = order_with_economic_caps
    _exec.SolanaLiveExecutor.swap = swap_with_cost_receipt
    _exec.SolanaLiveExecutor.sell = sell_with_atomic_account_close

    # Preserve the final exit-circuit composition: this becomes the rent-aware
    # inner close which the existing circuit breaker wraps at final startup.
    _rent._close_live_rent_aware = close_live_with_receipt_pnl
    if _live._close_live is _PREV_RENT_CLOSE:
        _live._close_live = close_live_with_receipt_pnl
    if _binding._close_bound_live is _PREV_RENT_CLOSE:
        _binding._close_bound_live = close_live_with_receipt_pnl

    _exec.SolanaLiveExecutor._execution_efficiency_patch = True
    print(
        "[solana-exec-efficiency] dynamic_total_fee_cap=true impact_slippage_guard=true "
        "atomic_full_close=true detailed_receipts=true unconstrained_tip=false"
    )


install()

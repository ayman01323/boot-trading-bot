from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import time
import zipfile
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

from .config import load_chains
from .multi_wallet_store import MultiWalletStore
from .solana_wallet_store import SolanaWalletStore
from .user_registry import all_users
from . import solana_sibot as _sol

ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
AUDIT_INTERVAL_SECONDS = 2 * 60 * 60
AUDIT_OVERLAP_SECONDS = 15 * 60
MAX_SOLANA_SIGNATURES_PER_WALLET = 2000
MAX_ETHERSCAN_ROWS_PER_KIND = 1000
RETENTION_RUNS = 84  # seven days at 12 runs/day

COMMON_HEADERS = [
    "telegram_id", "user_role", "user_status", "wallet_type", "wallet_id",
    "wallet_label", "wallet_address", "chain_slug", "chain_id", "source",
    "tx_hash", "time_epoch", "time_utc", "block_number", "status", "direction",
    "action", "asset", "token_address", "amount", "amount_raw", "native_delta",
    "fee_native", "from_address", "to_address", "method", "details_json",
    "explorer_url",
]

EXPLORERS = {
    1: "https://etherscan.io/tx/",
    56: "https://bscscan.com/tx/",
    137: "https://polygonscan.com/tx/",
    8453: "https://basescan.org/tx/",
    42161: "https://arbiscan.io/tx/",
}

SECRET_COLUMN_PARTS = (
    "private", "secret", "mnemonic", "seed", "keypair", "encrypted", "password",
)
RELEVANT_TABLE_PARTS = (
    "position", "execution", "attempt", "decision", "trade", "order", "queue",
    "transfer", "opportun", "profit", "route", "signal", "leader",
)
TIME_COLUMNS = (
    "created_at", "updated_at", "timestamp", "time_epoch", "observed_at_epoch",
    "event_ts", "entry_ts", "closed_at", "fetched_at", "seen_at",
)


def _utc_text(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _safe_name(value: str) -> str:
    value = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value))
    return value.strip("_")[:100] or "item"


def _decimal_raw(raw, decimals=18) -> str:
    try:
        return format(Decimal(str(raw or 0)) / (Decimal(10) ** int(decimals)), "f")
    except Exception:
        return "0"


def _write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in headers})
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _wallet_inventory(app):
    evm = MultiWalletStore(app.data_dir, app.csv_dir)
    sol = SolanaWalletStore(app.csv_dir, app.data_dir)
    users = []
    wallets = []
    for user in all_users(app.csv_dir, enabled_only=False):
        tid = str(user.get("telegram_id") or "").strip()
        if not tid:
            continue
        users.append(dict(user))
        for w in evm.list_wallets(tid, enabled_only=True):
            wallets.append({
                "telegram_id": tid,
                "user_role": str(user.get("role") or "USER"),
                "user_status": str(user.get("status") or ""),
                "wallet_type": "EVM",
                "wallet_id": str(w.get("wallet_id") or ""),
                "wallet_label": str(w.get("label") or ""),
                "wallet_address": str(w.get("address") or ""),
                "active": str(w.get("active") or "").lower() == "true",
            })
        for w in sol.list_wallets(tid, enabled_only=True):
            wallets.append({
                "telegram_id": tid,
                "user_role": str(user.get("role") or "USER"),
                "user_status": str(user.get("status") or ""),
                "wallet_type": "SOLANA",
                "wallet_id": str(w.get("wallet_id") or ""),
                "wallet_label": str(w.get("label") or ""),
                "wallet_address": str(w.get("address") or ""),
                "active": str(w.get("active") or "").lower() == "true",
            })
    return users, wallets


def _solana_signatures(app, address: str, since_epoch: int):
    out = []
    before = None
    while len(out) < MAX_SOLANA_SIGNATURES_PER_WALLET:
        opts = {"commitment": "confirmed", "limit": min(1000, MAX_SOLANA_SIGNATURES_PER_WALLET - len(out))}
        if before:
            opts["before"] = before
        rows = _sol._rpc(app, "getSignaturesForAddress", [address, opts]) or []
        if not rows:
            break
        stop = False
        for row in rows:
            ts = int(row.get("blockTime") or 0)
            if ts and ts < since_epoch:
                stop = True
                break
            out.append(row)
        if stop or len(rows) < opts["limit"]:
            break
        before = str(rows[-1].get("signature") or "")
        if not before:
            break
    return out[:MAX_SOLANA_SIGNATURES_PER_WALLET]


def _solana_programs(tx: dict) -> list[str]:
    out = []
    try:
        instructions = (((tx.get("transaction") or {}).get("message") or {}).get("instructions") or [])
        for ins in instructions:
            if not isinstance(ins, dict):
                continue
            value = ins.get("programId") or ins.get("program")
            if value:
                out.append(str(value))
    except Exception:
        pass
    return sorted(set(out))


def _solana_normalize(app, wallet: dict, row: dict) -> dict | None:
    sig = str(row.get("signature") or "")
    if not sig:
        return None
    tx = _sol._rpc(app, "getTransaction", [
        sig,
        {"commitment": "confirmed", "maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"},
    ])
    if not tx:
        return None
    address = wallet["wallet_address"]
    epoch = int(tx.get("blockTime") or row.get("blockTime") or 0)
    meta = tx.get("meta") or {}
    status = "SUCCESS" if meta.get("err") is None else "FAILED"
    token_deltas, pre_tokens, post_tokens, decimals = _sol._token_state(tx, address)
    native_delta = _sol._sol_delta(tx, address)
    classified = _sol.classify_swap(tx, address)
    action = "OTHER"
    direction = ""
    asset = "SOL"
    token_address = ""
    amount = ""
    amount_raw = ""
    if classified:
        action = str(classified.get("action") or "SWAP").upper()
        direction = "OUT" if action == "BUY" else "IN" if action == "SELL" else ""
        token_address = str(classified.get("mint") or "")
        asset = token_address or "TOKEN"
        amount_raw = str(classified.get("token_amount_raw") or "")
        amount = _decimal_raw(amount_raw, classified.get("decimals") or 0)
    elif token_deltas:
        action = "TOKEN_TRANSFER_OR_COMPLEX_SWAP"
    elif native_delta:
        action = "SOL_ONLY"
        direction = "IN" if native_delta > 0 else "OUT"
        amount = format(abs(native_delta), "f")
        amount_raw = str(int(abs(native_delta) * Decimal(1_000_000_000)))

    keys = _sol._account_keys(tx)
    payer_is_wallet = bool(keys and str(keys[0]) == address)
    fee_lamports = int(meta.get("fee") or 0) if payer_is_wallet else 0
    details = {
        "token_deltas_raw": token_deltas,
        "pre_tokens_raw": pre_tokens,
        "post_tokens_raw": post_tokens,
        "token_decimals": decimals,
        "programs": _solana_programs(tx),
        "solana_error": meta.get("err"),
    }
    return {
        **{k: wallet.get(k, "") for k in ("telegram_id", "user_role", "user_status", "wallet_type", "wallet_id", "wallet_label", "wallet_address")},
        "chain_slug": "solana",
        "chain_id": "solana",
        "source": "solana_rpc",
        "tx_hash": sig,
        "time_epoch": epoch,
        "time_utc": _utc_text(epoch) if epoch else "",
        "block_number": str(tx.get("slot") or row.get("slot") or ""),
        "status": status,
        "direction": direction,
        "action": action,
        "asset": asset,
        "token_address": token_address,
        "amount": amount,
        "amount_raw": amount_raw,
        "native_delta": format(native_delta, "f"),
        "fee_native": _decimal_raw(fee_lamports, 9),
        "from_address": address if direction == "OUT" else "",
        "to_address": address if direction == "IN" else "",
        "method": ";".join(_solana_programs(tx)),
        "details_json": json.dumps(details, separators=(",", ":"), default=str),
        "explorer_url": "https://solscan.io/tx/" + sig,
    }


def collect_solana(app, wallets: list[dict], since_epoch: int):
    rows = []
    errors = []
    for wallet in [w for w in wallets if w.get("wallet_type") == "SOLANA"]:
        address = wallet.get("wallet_address") or ""
        if not address:
            continue
        try:
            signatures = _solana_signatures(app, address, since_epoch)
            for sig_row in reversed(signatures):
                try:
                    normalized = _solana_normalize(app, wallet, sig_row)
                    if normalized and int(normalized.get("time_epoch") or 0) >= since_epoch:
                        rows.append(normalized)
                except Exception as exc:
                    errors.append({
                        "telegram_id": wallet.get("telegram_id"), "wallet": address,
                        "chain": "solana", "stage": "getTransaction",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
        except Exception as exc:
            errors.append({
                "telegram_id": wallet.get("telegram_id"), "wallet": address,
                "chain": "solana", "stage": "getSignaturesForAddress",
                "error": f"{type(exc).__name__}: {exc}",
            })
    return rows, errors


def _etherscan_get(session: requests.Session, api_key: str, chain_id: int, action: str, address: str):
    params = {
        "chainid": str(chain_id), "module": "account", "action": action,
        "address": address, "startblock": 0, "endblock": 999999999,
        "page": 1, "offset": MAX_ETHERSCAN_ROWS_PER_KIND, "sort": "desc",
        "apikey": api_key,
    }
    r = session.get(ETHERSCAN_V2, params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if str(payload.get("status")) == "0":
        message = str(payload.get("message") or "")
        result = payload.get("result")
        if "No transactions found" in message or "No transactions found" in str(result):
            return []
        raise RuntimeError(f"Etherscan {action}: {message} {result}")
    return payload.get("result") or []


def _evm_direction(address: str, from_address: str, to_address: str) -> str:
    a = str(address).lower()
    f = str(from_address or "").lower()
    t = str(to_address or "").lower()
    if f == a and t == a:
        return "SELF"
    if f == a:
        return "OUT"
    if t == a:
        return "IN"
    return "RELATED"


def _evm_row(wallet: dict, chain, source: str, item: dict) -> dict:
    address = wallet["wallet_address"]
    epoch = int(item.get("timeStamp") or 0)
    from_address = str(item.get("from") or "")
    to_address = str(item.get("to") or item.get("contractAddress") or "")
    direction = _evm_direction(address, from_address, to_address)
    status = "FAILED" if str(item.get("isError") or "0") == "1" or str(item.get("txreceipt_status") or "1") == "0" else "SUCCESS"
    token = str(item.get("contractAddress") or "") if source == "etherscan_token" else ""
    decimals = int(item.get("tokenDecimal") or 18) if source == "etherscan_token" else 18
    raw_value = str(item.get("value") or "0")
    asset = str(item.get("tokenSymbol") or token or "NATIVE") if source == "etherscan_token" else "NATIVE"
    fee = Decimal(0)
    if source == "etherscan_normal" and direction in {"OUT", "SELF"}:
        try:
            fee = Decimal(str(item.get("gasUsed") or 0)) * Decimal(str(item.get("gasPrice") or 0)) / Decimal(10**18)
        except Exception:
            fee = Decimal(0)
    tx_hash = str(item.get("hash") or "")
    explorer = EXPLORERS.get(int(chain.chain_id), "")
    details = {
        "nonce": item.get("nonce"), "gas": item.get("gas"), "gasUsed": item.get("gasUsed"),
        "gasPrice": item.get("gasPrice"), "tokenName": item.get("tokenName"),
        "functionName": item.get("functionName"), "input": str(item.get("input") or "")[:300],
        "traceId": item.get("traceId"),
    }
    return {
        **{k: wallet.get(k, "") for k in ("telegram_id", "user_role", "user_status", "wallet_type", "wallet_id", "wallet_label", "wallet_address")},
        "chain_slug": str(chain.slug), "chain_id": int(chain.chain_id), "source": source,
        "tx_hash": tx_hash, "time_epoch": epoch, "time_utc": _utc_text(epoch) if epoch else "",
        "block_number": str(item.get("blockNumber") or ""), "status": status,
        "direction": direction, "action": "TOKEN_TRANSFER" if source == "etherscan_token" else "INTERNAL_TRANSFER" if source == "etherscan_internal" else "EVM_TRANSACTION",
        "asset": asset, "token_address": token, "amount": _decimal_raw(raw_value, decimals),
        "amount_raw": raw_value, "native_delta": "", "fee_native": format(fee, "f"),
        "from_address": from_address, "to_address": to_address,
        "method": str(item.get("functionName") or ""),
        "details_json": json.dumps(details, separators=(",", ":"), default=str),
        "explorer_url": (explorer + tx_hash) if explorer and tx_hash else "",
    }


def collect_evm(app, wallets: list[dict], since_epoch: int):
    rows = []
    errors = []
    api_key = str(getattr(app, "etherscan_api_key", "") or "").strip()
    evm_wallets = [w for w in wallets if w.get("wallet_type") == "EVM"]
    if not evm_wallets:
        return rows, errors
    if not api_key:
        errors.append({"telegram_id": "*", "wallet": "*", "chain": "evm", "stage": "etherscan", "error": "ETHERSCAN_API_KEY is not configured; EVM public-history export skipped"})
        return rows, errors

    session = requests.Session()
    chains = [c for c in load_chains(app, enabled_only=True) if int(c.chain_id) != 0]
    for wallet in evm_wallets:
        address = wallet.get("wallet_address") or ""
        if not address:
            continue
        for chain in chains:
            for action, source in (("txlist", "etherscan_normal"), ("tokentx", "etherscan_token"), ("txlistinternal", "etherscan_internal")):
                try:
                    items = _etherscan_get(session, api_key, int(chain.chain_id), action, address)
                    for item in items:
                        epoch = int(item.get("timeStamp") or 0)
                        if epoch and epoch < since_epoch:
                            continue
                        rows.append(_evm_row(wallet, chain, source, item))
                    time.sleep(0.22)
                except Exception as exc:
                    errors.append({
                        "telegram_id": wallet.get("telegram_id"), "wallet": address,
                        "chain": str(chain.slug), "stage": action,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
    return rows, errors


def _tx_key(row: dict) -> str:
    return "|".join([
        str(row.get("telegram_id") or ""), str(row.get("wallet_type") or ""),
        str(row.get("wallet_address") or "").lower(), str(row.get("chain_slug") or ""),
        str(row.get("source") or ""), str(row.get("tx_hash") or ""),
        str(row.get("token_address") or "").lower(), str(row.get("direction") or ""),
        str(row.get("amount_raw") or ""),
    ])


def _update_cumulative(root: Path, new_rows: list[dict]):
    path = root / "cumulative_all_transactions.csv"
    existing = _read_csv(path)
    merged = {}
    for row in existing + new_rows:
        merged[_tx_key(row)] = row
    rows = sorted(merged.values(), key=lambda r: (int(r.get("time_epoch") or 0), str(r.get("tx_hash") or "")))
    _write_csv(path, COMMON_HEADERS, rows)
    return path, len(rows)


def _export_db_tables(app, run_dir: Path, since_epoch: int):
    exported = []
    db_root = run_dir / "bot_db"
    for db_path in Path(app.data_dir).rglob("*.sqlite3"):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            tables = [str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            for table in tables:
                if not any(part in table.lower() for part in RELEVANT_TABLE_PARTS):
                    continue
                info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                cols = [str(r[1]) for r in info]
                safe_cols = [c for c in cols if not any(part in c.lower() for part in SECRET_COLUMN_PARTS)]
                if not safe_cols:
                    continue
                time_col = next((c for c in TIME_COLUMNS if c in cols), None)
                select = ",".join('"' + c.replace('"', '""') + '"' for c in safe_cols)
                if time_col:
                    sql = f'SELECT {select} FROM "{table}" WHERE COALESCE("{time_col}",0)>=? ORDER BY "{time_col}" DESC LIMIT 10000'
                    rows = [dict(r) for r in conn.execute(sql, (int(since_epoch),)).fetchall()]
                else:
                    sql = f'SELECT {select} FROM "{table}" ORDER BY rowid DESC LIMIT 2000'
                    rows = [dict(r) for r in conn.execute(sql).fetchall()]
                if not rows:
                    continue
                out = db_root / f"{_safe_name(db_path.stem)}__{_safe_name(table)}.csv"
                _write_csv(out, safe_cols, rows)
                exported.append(str(out.relative_to(run_dir)))
            conn.close()
        except Exception:
            continue
    return exported


def _copy_strategy_snapshot(app, run_dir: Path):
    copied = []
    target = run_dir / "strategy_snapshot"
    csv_root = Path(app.csv_dir)
    for path in csv_root.rglob("*.csv"):
        name = path.name.lower()
        if any(part in name for part in ("private", "secret", "key", "seed", "mnemonic")):
            continue
        if not any(part in name for part in (
            "solana", "trading_settings", "risk_settings", "copy", "leader", "top20",
            "live_opportun", "route_scanner", "profit", "wallet",
        )):
            continue
        try:
            if path.stat().st_size > 5_000_000:
                continue
            rel = path.relative_to(csv_root)
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst)
            copied.append(str(dst.relative_to(run_dir)))
        except Exception:
            continue
    return copied


def _zip_run(run_dir: Path, zip_path: Path):
    tmp = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(run_dir)))
    os.replace(tmp, zip_path)


def _prune(root: Path):
    runs = sorted([p for p in root.glob("run_*") if p.is_dir()], key=lambda p: p.name, reverse=True)
    for path in runs[RETENTION_RUNS:]:
        shutil.rmtree(path, ignore_errors=True)
    zips = sorted([p for p in root.glob("transaction_audit_*.zip") if p.is_file()], key=lambda p: p.name, reverse=True)
    for path in zips[RETENTION_RUNS:]:
        try:
            path.unlink()
        except Exception:
            pass


def run_transaction_audit(app, *, hours: float = 2.0):
    now = int(time.time())
    since = now - max(300, int(float(hours) * 3600)) - AUDIT_OVERLAP_SECONDS
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    root = Path(app.data_dir) / "transaction_audits"
    run_dir = root / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    users, wallets = _wallet_inventory(app)
    wallet_headers = [
        "telegram_id", "user_role", "user_status", "wallet_type", "wallet_id",
        "wallet_label", "wallet_address", "active",
    ]
    _write_csv(run_dir / "wallet_inventory.csv", wallet_headers, wallets)

    sol_rows, sol_errors = collect_solana(app, wallets, since)
    evm_rows, evm_errors = collect_evm(app, wallets, since)
    all_rows = sorted(sol_rows + evm_rows, key=lambda r: (int(r.get("time_epoch") or 0), str(r.get("tx_hash") or "")))

    _write_csv(run_dir / "all_transactions.csv", COMMON_HEADERS, all_rows)
    _write_csv(run_dir / "solana_transactions.csv", COMMON_HEADERS, sol_rows)
    _write_csv(run_dir / "evm_transactions.csv", COMMON_HEADERS, evm_rows)
    errors = sol_errors + evm_errors
    error_headers = ["telegram_id", "wallet", "chain", "stage", "error"]
    _write_csv(run_dir / "collection_errors.csv", error_headers, errors)

    db_exports = _export_db_tables(app, run_dir, since)
    strategy_files = _copy_strategy_snapshot(app, run_dir)
    cumulative_path, cumulative_rows = _update_cumulative(root, all_rows)

    by_source = defaultdict(int)
    by_user = defaultdict(int)
    for row in all_rows:
        by_source[str(row.get("source") or "unknown")] += 1
        by_user[str(row.get("telegram_id") or "unknown")] += 1

    summary = {
        "generated_epoch": now,
        "generated_utc": _utc_text(now),
        "requested_hours": float(hours),
        "overlap_minutes": AUDIT_OVERLAP_SECONDS // 60,
        "window_start_epoch": since,
        "window_start_utc": _utc_text(since),
        "registered_users": len(users),
        "enabled_wallets": len(wallets),
        "solana_wallets": sum(1 for w in wallets if w.get("wallet_type") == "SOLANA"),
        "evm_wallets": sum(1 for w in wallets if w.get("wallet_type") == "EVM"),
        "transaction_event_rows": len(all_rows),
        "solana_transactions": len(sol_rows),
        "evm_event_rows": len(evm_rows),
        "collection_errors": len(errors),
        "by_source": dict(sorted(by_source.items())),
        "by_telegram_id": dict(sorted(by_user.items())),
        "bot_db_exports": db_exports,
        "strategy_snapshot_files": strategy_files,
        "cumulative_ledger": str(cumulative_path),
        "cumulative_rows": cumulative_rows,
        "privacy": "No private keys, seed phrases, encrypted key material or passwords are included.",
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    zip_path = root / f"transaction_audit_{stamp}.zip"
    _zip_run(run_dir, zip_path)
    latest = root / "latest_all_ids.zip"
    tmp_latest = latest.with_suffix(".zip.tmp")
    shutil.copy2(zip_path, tmp_latest)
    os.replace(tmp_latest, latest)
    _prune(root)
    return {**summary, "run_dir": str(run_dir), "zip_path": str(zip_path), "latest_zip": str(latest)}

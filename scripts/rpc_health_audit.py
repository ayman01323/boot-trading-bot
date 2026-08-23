from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from websockets.sync.client import connect as ws_connect


EVM_FAST_WS_CHAINS = {137, 42161, 56, 8453}


def _provider_label(url: str) -> str:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    mapping = (
        ("alchemy.com", "Alchemy"),
        ("helius-rpc.com", "Helius"),
        ("helius.xyz", "Helius"),
        ("quiknode.pro", "QuickNode"),
        ("quicknode.com", "QuickNode"),
        ("infura.io", "Infura"),
        ("ankr.com", "Ankr"),
        ("publicnode.com", "PublicNode"),
        ("drpc.org", "dRPC"),
        ("chainstack.com", "Chainstack"),
        ("blastapi.io", "Blast"),
        ("llamarpc.com", "LlamaRPC"),
    )
    for needle, label in mapping:
        if needle in host:
            return label
    return "Other/Custom" if host else "Unknown"


def _error_kind(exc: Exception) -> str:
    text = str(exc).lower()
    if "timed out" in text or "timeout" in text:
        return "TIMEOUT"
    if "ssl" in text or "tls" in text or "certificate" in text:
        return "TLS"
    if "name or service" in text or "resolve" in text or "dns" in text:
        return "DNS"
    if "429" in text or "rate limit" in text or "too many" in text:
        return "RATE_LIMIT"
    if "401" in text or "403" in text or "unauthor" in text or "forbidden" in text:
        return "AUTH"
    if "connection refused" in text:
        return "CONNECTION_REFUSED"
    return type(exc).__name__[:80]


def _bool(value, default=True) -> bool:
    if value is None or str(value).strip() == "":
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _http_probe(url: str, chain_id: int, chain_type: str, timeout: float) -> dict:
    started = time.perf_counter()
    try:
        if chain_type == "SOLANA":
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getHealth"}
        else:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
        response = requests.post(url, json=payload, timeout=timeout)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
        if response.status_code != 200:
            return {"ok": False, "latency_ms": elapsed_ms, "error": f"HTTP_{response.status_code}"}
        body = response.json()
        if body.get("error"):
            error = body.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            return {"ok": False, "latency_ms": elapsed_ms, "error": f"RPC_ERROR_{code}"}
        result = body.get("result")
        if chain_type == "SOLANA":
            ok = str(result).lower() == "ok"
            return {
                "ok": ok,
                "latency_ms": elapsed_ms,
                "error": "" if ok else "UNEXPECTED_HEALTH",
                "reported_chain_id": "solana-mainnet" if ok else None,
            }
        try:
            actual = int(str(result), 16)
        except Exception:
            return {"ok": False, "latency_ms": elapsed_ms, "error": "BAD_CHAIN_ID"}
        return {
            "ok": actual == int(chain_id),
            "latency_ms": elapsed_ms,
            "error": "" if actual == int(chain_id) else "WRONG_CHAIN",
            "reported_chain_id": actual,
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "error": _error_kind(exc),
        }


def _recv_id(ws, request_id: int, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
        message = json.loads(raw)
        if message.get("id") == request_id:
            return message
    return None


def _evm_ws_probe(url: str, chain_id: int, params: list, mode: str, timeout: float) -> dict:
    started = time.perf_counter()
    try:
        with ws_connect(
            url,
            open_timeout=timeout,
            close_timeout=min(3.0, timeout),
            ping_interval=20,
            ping_timeout=10,
            max_size=2 * 1024 * 1024,
        ) as ws:
            ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}))
            chain_reply = _recv_id(ws, 1, timeout)
            if not chain_reply or chain_reply.get("error"):
                return {"ok": False, "latency_ms": round((time.perf_counter()-started)*1000, 2), "error": "CHAIN_ID_FAILED", "mode": mode}
            try:
                actual = int(str(chain_reply.get("result")), 16)
            except Exception:
                return {"ok": False, "latency_ms": round((time.perf_counter()-started)*1000, 2), "error": "BAD_CHAIN_ID", "mode": mode}
            if actual != int(chain_id):
                return {"ok": False, "latency_ms": round((time.perf_counter()-started)*1000, 2), "error": "WRONG_CHAIN", "reported_chain_id": actual, "mode": mode}
            ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "eth_subscribe", "params": params}, separators=(",", ":")))
            sub = _recv_id(ws, 2, timeout)
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
            if not sub or sub.get("error") or not sub.get("result"):
                return {"ok": False, "latency_ms": elapsed_ms, "error": "SUBSCRIBE_FAILED", "reported_chain_id": actual, "mode": mode}
            return {"ok": True, "latency_ms": elapsed_ms, "error": "", "reported_chain_id": actual, "mode": mode}
    except Exception as exc:
        return {"ok": False, "latency_ms": round((time.perf_counter()-started)*1000, 2), "error": _error_kind(exc), "mode": mode}


def _solana_ws_probe(url: str, leader: str, timeout: float) -> dict:
    started = time.perf_counter()
    try:
        with ws_connect(
            url,
            open_timeout=timeout,
            close_timeout=min(3.0, timeout),
            ping_interval=20,
            ping_timeout=10,
            max_size=2 * 1024 * 1024,
        ) as ws:
            if leader:
                params = [{"mentions": [leader]}, {"commitment": "confirmed"}]
                method = "logsSubscribe"
                mode = "leader_logs"
            else:
                params = []
                method = "slotSubscribe"
                mode = "slot_probe_idle_no_leaders"
            ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, separators=(",", ":")))
            reply = _recv_id(ws, 1, timeout)
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
            ok = bool(reply and reply.get("result") is not None and not reply.get("error"))
            return {"ok": ok, "latency_ms": elapsed_ms, "error": "" if ok else "SUBSCRIBE_FAILED", "mode": mode}
    except Exception as exc:
        return {"ok": False, "latency_ms": round((time.perf_counter()-started)*1000, 2), "error": _error_kind(exc), "mode": "leader_logs" if leader else "slot_probe_idle_no_leaders"}


def audit_rpc_health(*, timeout_seconds: float = 8.0) -> dict:
    """Probe production RPC/WSS connectivity without returning URLs or credentials."""
    from learnerbot.config import AppSettings, load_chains
    from learnerbot import polygon_websocket_patch as evm_ws
    from learnerbot import solana_websocket_patch as sol_ws

    timeout = max(2.0, min(float(timeout_seconds), 10.0))
    app = AppSettings.load()
    chains = load_chains(app, enabled_only=False)
    meta = {
        int(c.chain_id): {
            "slug": str(c.slug),
            "name": str(c.name),
            "type": str(c.type).upper(),
            "enabled": bool(c.enabled),
        }
        for c in chains
    }

    results: list[dict] = []
    rpc_path = Path(app.csv_dir) / "rpc_endpoints.csv"
    rows: list[dict] = []
    if rpc_path.exists():
        with rpc_path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))

    endpoint_index = 0
    ws_seen: set[int] = set()
    for row in rows:
        if not _bool(row.get("enabled"), True):
            continue
        try:
            chain_id = int(str(row.get("chain_id") or "0").strip())
        except Exception:
            continue
        info = meta.get(chain_id, {"slug": str(chain_id), "type": "EVM", "enabled": True})
        priority = str(row.get("priority") or "999").strip()
        url = str(row.get("url") or "").strip()
        if url.startswith(("https://", "http://")):
            endpoint_index += 1
            probe = _http_probe(url, chain_id, info["type"], timeout)
            results.append({
                "kind": "RPC",
                "endpoint_index": endpoint_index,
                "chain_id": chain_id,
                "chain": info["slug"],
                "provider": _provider_label(url),
                "priority": priority,
                "status": "OK" if probe["ok"] else "FAIL",
                "latency_ms": probe.get("latency_ms"),
                "error": probe.get("error") or "",
                "reported_chain_id": probe.get("reported_chain_id"),
            })

        ws_url = str(row.get("ws_url") or "").strip() or str(row.get("websocket_url") or "").strip()
        if ws_url.startswith(("wss://", "ws://")):
            ws_seen.add(chain_id)
            endpoint_index += 1
            leaders = evm_ws._leader_addresses(app, chain_id)[:25] if chain_id in EVM_FAST_WS_CHAINS else []
            params, mode = evm_ws._subscription_request(chain_id, ws_url, leaders)
            probe = _evm_ws_probe(ws_url, chain_id, params, mode, timeout)
            results.append({
                "kind": "WSS",
                "endpoint_index": endpoint_index,
                "chain_id": chain_id,
                "chain": info["slug"],
                "provider": _provider_label(ws_url),
                "priority": priority,
                "status": "OK" if probe["ok"] else "FAIL",
                "latency_ms": probe.get("latency_ms"),
                "error": probe.get("error") or "",
                "reported_chain_id": probe.get("reported_chain_id"),
                "mode": probe.get("mode"),
                "leader_count_used": len(leaders),
            })

    # The fast EVM WebSocket worker only uses these four chains. Missing WSS is
    # a speed/fallback issue, not an HTTP RPC outage.
    for chain_id in sorted(EVM_FAST_WS_CHAINS):
        info = meta.get(chain_id)
        if info and info.get("enabled") and chain_id not in ws_seen:
            results.append({
                "kind": "WSS",
                "endpoint_index": None,
                "chain_id": chain_id,
                "chain": info["slug"],
                "provider": "None",
                "priority": None,
                "status": "MISSING",
                "latency_ms": None,
                "error": "NO_ENABLED_WS_URL_IN_RPC_ENDPOINTS_CSV",
                "reported_chain_id": None,
                "mode": "fallback_http_poll_only",
                "leader_count_used": 0,
            })

    # Solana has its own settings/env override path. Resolve it exactly as the
    # running service does, but never return the URL or key-bearing query string.
    sol_rpc = str(os.environ.get("SOLANA_RPC_URL") or "").strip()
    if not sol_rpc:
        sol_settings = Path(app.csv_dir) / "solana_settings.csv"
        if sol_settings.exists():
            with sol_settings.open("r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    if str(row.get("setting") or "").strip() == "rpc_url":
                        sol_rpc = str(row.get("value") or "").strip()
                        break
    if not sol_rpc:
        sol_rpc = "https://api.mainnet-beta.solana.com"

    if sol_rpc.startswith(("https://", "http://")):
        probe = _http_probe(sol_rpc, -101, "SOLANA", timeout)
        results.append({
            "kind": "RPC",
            "endpoint_index": None,
            "chain_id": -101,
            "chain": "solana",
            "provider": _provider_label(sol_rpc),
            "priority": "runtime",
            "status": "OK" if probe["ok"] else "FAIL",
            "latency_ms": probe.get("latency_ms"),
            "error": probe.get("error") or "",
            "reported_chain_id": probe.get("reported_chain_id"),
        })

    sol_ws_url = sol_ws._solana_ws_url(app)
    leaders = sol_ws._selected_leaders(app)
    if sol_ws_url:
        probe = _solana_ws_probe(sol_ws_url, leaders[0] if leaders else "", timeout)
        results.append({
            "kind": "WSS",
            "endpoint_index": None,
            "chain_id": -101,
            "chain": "solana",
            "provider": _provider_label(sol_ws_url),
            "priority": "runtime",
            "status": "OK" if probe["ok"] else "FAIL",
            "latency_ms": probe.get("latency_ms"),
            "error": probe.get("error") or "",
            "reported_chain_id": "solana-subscription" if probe["ok"] else None,
            "mode": probe.get("mode"),
            "selected_leaders": len(leaders),
        })
    else:
        results.append({
            "kind": "WSS",
            "endpoint_index": None,
            "chain_id": -101,
            "chain": "solana",
            "provider": "None",
            "priority": "runtime",
            "status": "MISSING",
            "latency_ms": None,
            "error": "NO_SOLANA_WS_URL_RESOLVED",
            "reported_chain_id": None,
            "mode": "fallback_http_poll_only",
            "selected_leaders": len(leaders),
        })

    rpc_rows = [row for row in results if row.get("kind") == "RPC"]
    ws_rows = [row for row in results if row.get("kind") == "WSS"]
    problems = [
        {
            "kind": row.get("kind"),
            "chain": row.get("chain"),
            "provider": row.get("provider"),
            "status": row.get("status"),
            "error": row.get("error"),
        }
        for row in results
        if row.get("status") not in {"OK"}
    ]
    return {
        "schema_version": 1,
        "generated_epoch": int(time.time()),
        "scope": "READ_ONLY_RPC_WSS_HEALTH_NO_URLS_NO_CREDENTIALS",
        "rpc": rpc_rows,
        "websockets": ws_rows,
        "summary": {
            "rpc_tested": len(rpc_rows),
            "rpc_ok": sum(1 for row in rpc_rows if row.get("status") == "OK"),
            "rpc_failed": sum(1 for row in rpc_rows if row.get("status") != "OK"),
            "ws_tested_or_required": len(ws_rows),
            "ws_ok": sum(1 for row in ws_rows if row.get("status") == "OK"),
            "ws_failed_or_missing": sum(1 for row in ws_rows if row.get("status") != "OK"),
            "problem_count": len(problems),
        },
        "problems": problems,
        "privacy": {
            "rpc_urls_returned": False,
            "websocket_urls_returned": False,
            "api_keys_returned": False,
            "wallet_addresses_returned": False,
        },
    }

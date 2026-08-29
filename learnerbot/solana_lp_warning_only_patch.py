from __future__ import annotations

"""Owner-approved LP warning-only policy for SiLearn/Solana.

SiLearn — 2026-08-29 13:28 BST — Subject: Downgrade LP lock/provider warnings to Telegram reference only

Only LP ownership/lock/provider-concentration signals are downgraded. They remain
visible as Telegram reference warnings, but cannot block LIVE by themselves.
All other PoolCheck decisions, structural token-security blocks, external-provider
fail-closed behaviour, DexScreener pool checks and Jupiter reverse-depth checks
remain unchanged.
"""

import hashlib
import html
from contextlib import closing
from decimal import Decimal

from . import solana_live_patch as _live
from . import solana_pool_risk_gate as _pool
from . import solana_sibot as _sol

BOT_NAME = "SiLearn"
CHANGE_APPROVED_UTC = "2026-08-29T12:28:36Z"
CHANGE_APPROVED_BST = "2026-08-29T13:28:36+01:00"
CHANGE_SUBJECT = "Downgrade LP lock/provider warnings to Telegram reference only"
WARNING_POLICY = "once_per_account_mint_lp_warning_set"
_STATE_PREFIX = "telegram_lp_warning_once:v1:"

_LP_ONLY_TERMS = (
    "lp unlocked",
    "liquidity unlocked",
    "unlocked lp",
    "large amount of lp unlocked",
    "lp locked",
    "liquidity locked",
    "low amount of lp providers",
    "low amount of liquidity providers",
    "lp provider concentration",
    "liquidity provider concentration",
    "lp providers",
    "liquidity providers",
)

_STRUCTURAL_DANGER_TERMS = (
    "freeze authority",
    "mint authority",
    "permanent delegate",
    "honeypot",
    "rugged",
    "blacklist",
    "non-transferable",
    "default account state",
    "transfer hook",
    "malicious transfer",
)

_PREV_EVALUATE_RUGCHECK = _pool.evaluate_rugcheck
_PREV_EVALUATE_LIVE_POOL_RISK = _pool.evaluate_live_pool_risk


def _risk_text(risk: dict) -> str:
    return " ".join(str(risk.get(k) or "") for k in ("name", "description", "value")).lower()


def _is_lp_only(text: str) -> bool:
    value = str(text or "").lower()
    return any(term in value for term in _LP_ONLY_TERMS)


def _has_structural_danger(text: str) -> bool:
    value = str(text or "").lower()
    return any(term in value for term in _STRUCTURAL_DANGER_TERMS)


def _lp_warning_details(summary: dict) -> list[str]:
    out: list[str] = []
    risks = [r for r in (summary or {}).get("risks") or [] if isinstance(r, dict)]
    for risk in risks:
        text = _risk_text(risk)
        if not _is_lp_only(text) or _has_structural_danger(text):
            continue
        name = str(risk.get("name") or "LP liquidity warning").strip()
        value = str(risk.get("value") or "").strip()
        level = str(risk.get("level") or "").strip().lower()
        label = name
        if value:
            label += f" ({value})"
        if level in {"danger", "critical", "severe"}:
            label += f" [{level.upper()} reference]"
        if label not in out:
            out.append(label)

    try:
        locked = Decimal(str((summary or {}).get("lpLockedPct")))
    except Exception:
        locked = None
    if locked is not None and locked < Decimal("50"):
        label = f"LP locked {locked}% (reference only)"
        if label not in out:
            out.append(label)
    return out


def _structural_risk_present(summary: dict) -> bool:
    return any(
        _has_structural_danger(_risk_text(risk))
        for risk in (summary or {}).get("risks") or []
        if isinstance(risk, dict)
    )


def _lp_only_severe_set(summary: dict) -> bool:
    """True only when every explicitly severe provider risk is LP-specific."""
    severe = []
    for risk in (summary or {}).get("risks") or []:
        if not isinstance(risk, dict):
            continue
        level = str(risk.get("level") or "").lower()
        if level in {"danger", "critical", "severe"}:
            severe.append(_risk_text(risk))
    return bool(severe) and all(_is_lp_only(text) and not _has_structural_danger(text) for text in severe)


def _may_downgrade_to_warning(result: dict, summary: dict) -> bool:
    code = str((result or {}).get("reason_code") or "").upper()
    decision = str((result or {}).get("decision") or "").upper()
    evidence = dict((result or {}).get("evidence") or {})

    if _structural_risk_present(summary):
        return False
    if code in {"LP_CONCENTRATION_RISK", "LP_REVALIDATION_REQUIRED"}:
        return True
    if decision == "HARD_BLOCK" and code == "TOKEN_SECURITY_SEVERE":
        blocking = str(evidence.get("rugcheck_blocking_risk") or "")
        if blocking:
            return _is_lp_only(blocking) and not _has_structural_danger(blocking)
        # Score-only severe result: downgrade only when the report's explicit
        # severe findings are all LP-specific. Unknown/non-LP score risk stays blocked.
        return _lp_only_severe_set(summary)
    return False


def evaluate_rugcheck_lp_warning_only(summary: dict, cfg: dict) -> dict:
    result = dict(_PREV_EVALUATE_RUGCHECK(summary, cfg) or {})
    warnings = _lp_warning_details(summary)
    if not warnings:
        return result

    evidence = dict(result.get("evidence") or {})
    evidence.update({
        "lp_warning_only": True,
        "lp_warning_messages": warnings,
        "lp_warning_policy": WARNING_POLICY,
        "lp_revalidation_required": False,
        "lp_lock_blocking_disabled": True,
        "lp_provider_diversification_blocking_disabled": True,
        "lp_specific_severe_blocking_disabled": True,
    })

    if _may_downgrade_to_warning(result, summary):
        return _pool._decision(
            "PASS",
            "LP_WARNING_ONLY",
            "LP lock/provider concentration findings are Telegram reference warnings only; continue with all other PoolCheck gates",
            evidence,
        )

    result["evidence"] = evidence
    return result


def _warning_key(tid: str, mint: str, warnings: list[str]) -> str:
    raw = "\x1f".join((str(tid), str(mint), *sorted(str(v) for v in warnings))).encode("utf-8", "replace")
    return _STATE_PREFIX + hashlib.sha256(raw).hexdigest()


def _already_warned(app, key: str) -> bool:
    try:
        with closing(_sol.connect(app)) as conn:
            return bool(_sol._state(conn, key, ""))
    except Exception:
        return False


def _mark_warned(app, key: str) -> None:
    with closing(_sol.connect(app)) as conn:
        _sol._set_state(conn, key, CHANGE_APPROVED_UTC)


def _send_reference_warning(app, event: dict, cfg: dict, warnings: list[str]) -> None:
    mint = str(event.get("mint") or "").strip()
    leader = str(event.get("leader_wallet") or "").strip()
    signal = str(event.get("signature") or event.get("event_id") or "").strip()
    targets = _pool._eligible_live_users(app, event, cfg)
    if not targets:
        return

    warning_lines = "\n".join(f"• {html.escape(str(item))}" for item in warnings[:8])
    asset_html = html.escape(mint)
    leader_html = html.escape(leader)
    signal_html = html.escape(signal)
    for tid, _ in targets:
        key = _warning_key(tid, mint, warnings)
        if _already_warned(app, key):
            continue
        links = [f'Asset: <a href="https://solscan.io/token/{asset_html}">{asset_html}</a>']
        if leader:
            links.append(f'Leader: <a href="https://solscan.io/account/{leader_html}">{leader_html}</a>')
        if signal:
            links.append(f'Signal: <a href="https://solscan.io/tx/{signal_html}">{signal_html}</a>')
        message = (
            "⚠️ <b>SiLearn LP reference warning</b>\n"
            + "\n".join(links)
            + "\n\n"
            + warning_lines
            + "\n\n<b>Decision impact: WARNING ONLY</b>\n"
              "These LP lock/provider signals do not block the BUY. Pool depth, age, activity, "
              "price consistency, liquidity-collapse, reverse-sell depth and all other safety/execution gates still decide."
        )
        try:
            _live._notify(app, str(tid), message)
            _mark_warned(app, key)
        except Exception as exc:
            print("[silearn-lp-warning-only] telegram_warning_error=%s" % str(exc)[:220])


def evaluate_live_pool_risk_lp_warning_only(app, event: dict, cfg: dict, *, probe_sol=None) -> dict:
    result = dict(_PREV_EVALUATE_LIVE_POOL_RISK(app, event, cfg, probe_sol=probe_sol) or {})
    warnings = list((result.get("evidence") or {}).get("lp_warning_messages") or [])
    if _pool._severity(result) == 0 and warnings:
        _send_reference_warning(app, event, cfg, warnings)
    return result


def install() -> None:
    if getattr(_pool, "_silearn_lp_warning_only_installed", False):
        return
    _pool.evaluate_rugcheck = evaluate_rugcheck_lp_warning_only
    _pool.evaluate_live_pool_risk = evaluate_live_pool_risk_lp_warning_only
    _pool._silearn_lp_warning_only_installed = True
    print(
        "[silearn-lp-warning-only] bot=SiLearn approved=2026-08-29T12:28:36Z "
        "bst=2026-08-29T13:28:36+01:00 lp_lock=WARNING_ONLY "
        "lp_specific_severe=WARNING_ONLY lp_provider_diversification=WARNING_ONLY "
        "telegram_reference=once_per_account_mint_warning_set structural_danger=HARD_BLOCK "
        "other_pool_gates=PRESERVED"
    )


install()

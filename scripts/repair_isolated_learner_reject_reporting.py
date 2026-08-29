from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def replace_once(path: Path, old: str, new: str, marker: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return False
    if old not in text:
        raise RuntimeError(f"expected patch anchor not found in {path}: {marker}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def patch_outbound_telegram(root: Path) -> bool:
    path = root / "learnerbot" / "telegram_ui.py"
    old = "def start_menu_thread(app):\n    if not app.telegram_bot_token:return None\n"
    new = (
        "def start_menu_thread(app):\n"
        "    if os.getenv('LEARNER_TELEGRAM_OUTBOUND_ONLY','').strip().lower() in {'1','true','yes','on'}:\n"
        "        print('[telegram-menu] outbound-only mode: polling disabled')\n"
        "        return None\n"
        "    if not app.telegram_bot_token:return None\n"
    )
    return replace_once(path, old, new, "[telegram-menu] outbound-only mode: polling disabled")


def install_publisher(repo: Path, root: Path) -> bool:
    src = repo / "learnerbot" / "rejected_opportunity_publisher.py"
    dst = root / "learnerbot" / "rejected_opportunity_publisher.py"
    before = dst.read_bytes() if dst.exists() else None
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return before != dst.read_bytes()


def _upgrade_existing_sender(path: Path) -> bool:
    """Upgrade older injected sender code without touching trading logic."""
    text = path.read_text(encoding="utf-8")
    changed = False

    sender_signature = (
        "def _send_reject_report(app, tid: str, event: dict, action: dict, *, test_only: bool = False) -> bool:\n"
    )
    error_helper = r'''def _telegram_error_text(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    description = ""
    if response is not None:
        try:
            data = response.json()
            description = str(data.get("description") or "")[:180]
        except Exception:
            description = ""
    if description:
        return f"status={status or 'unknown'} description={description}"
    return f"status={status or 'unknown'} type={type(exc).__name__}"


'''
    if sender_signature in text and "def _telegram_error_text(exc: Exception)" not in text:
        text = text.replace(sender_signature, error_helper + sender_signature, 1)
        changed = True

    old_send = r'''    response = send_message(
        app.telegram_bot_token,
        str(tid),
        "\n".join(lines),
        parse_mode="HTML",
        protect_content=True,
    )
    return bool((response or {}).get("message_id"))
'''
    new_send = r'''    try:
        sent_count = send_message(
            app.telegram_bot_token,
            str(tid),
            "\n".join(lines),
            parse_mode="HTML",
            protect_content=True,
        )
    except Exception as exc:
        raise RuntimeError("telegram_send_failed " + _telegram_error_text(exc)) from None
    return int(sent_count or 0) > 0
'''
    if old_send in text:
        text = text.replace(old_send, new_send, 1)
        changed = True

    if sender_signature in text and "return bool((response or {}).get(\"message_id\"))" in text:
        raise RuntimeError("legacy Learner reject sender remains after upgrade")

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def patch_final_reject_consumer(root: Path) -> bool:
    path = root / "learnerbot" / "solana_leader_cursor_reliability_patch.py"
    changed = False
    changed |= replace_once(
        path,
        "import time\nfrom contextlib import closing\n\nfrom . import solana_sibot as _sol\n",
        "import html\nimport time\nfrom contextlib import closing\n\nfrom . import solana_sibot as _sol\nfrom .rejected_opportunity_publisher import publish_rejection\nfrom .solana_live_patch import live_enabled\nfrom .telegram import send_message\nfrom .user_registry import all_users\n",
        "from .rejected_opportunity_publisher import publish_rejection",
    )

    helper_anchor = """def _retryable_reason(text: str) -> bool:\n    value = str(text or \"\").lower()\n    return any(x in value for x in (\n        \"429\", \"rate limit\", \"too many requests\", \"timeout\", \"timed out\",\n        \"temporarily unavailable\", \"service unavailable\", \"connection reset\",\n        \"connection aborted\", \"gateway timeout\", \"jupiter quote failed\",\n    ))\n\n\n"""
    helper = helper_anchor + r'''_REJECT_REPORT_DEDUP: dict[tuple[str, str, str], float] = {}


def _reject_class(action: dict) -> str:
    explicit = str(action.get("pool_risk_code") or action.get("rejection_class") or "").strip()
    if explicit:
        return explicit[:80]
    reason = str(action.get("reason") or "").strip()
    head = reason.split(":", 1)[0].strip().upper().replace(" ", "_").replace("-", "_")
    return (head or "LEARNER_REJECT")[:80]


def _reject_targets(app, event: dict, action: dict) -> list[str]:
    tid = str(action.get("telegram_id") or "").strip()
    if tid:
        return [tid]
    wallet = str(event.get("leader_wallet") or "")
    out: list[str] = []
    try:
        for user in all_users(app.csv_dir, enabled_only=True):
            candidate = str(user.get("telegram_id") or "").strip()
            if not candidate or not live_enabled(app, candidate):
                continue
            if not _sol._sibot._bool(_sol._sibot.user_settings(app, candidate, 0).get("enabled"), False):
                continue
            if wallet and _sol._leader_rank(app, candidate, wallet) is None:
                continue
            out.append(candidate)
    except Exception as exc:
        print("[learner-reject-report] target_error=%s:%s" % (type(exc).__name__, str(exc)[:160]))
    return list(dict.fromkeys(out))


def _short(value: str, n: int = 22) -> str:
    value = str(value or "")
    if len(value) <= n:
        return value
    return value[:10] + "…" + value[-8:]


def _telegram_error_text(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    description = ""
    if response is not None:
        try:
            data = response.json()
            description = str(data.get("description") or "")[:180]
        except Exception:
            description = ""
    if description:
        return f"status={status or 'unknown'} description={description}"
    return f"status={status or 'unknown'} type={type(exc).__name__}"


def _send_reject_report(app, tid: str, event: dict, action: dict, *, test_only: bool = False) -> bool:
    if not getattr(app, "telegram_bot_token", ""):
        return False
    reason = str(action.get("reason") or "unspecified rejection")
    mint = str(event.get("mint") or action.get("mint") or "unknown")
    wallet = str(event.get("leader_wallet") or action.get("leader_wallet") or "")
    signature = str(event.get("signature") or event.get("event_id") or action.get("signature") or "")
    lines = [
        "⛔ <b>LEARNER REJECTED OPPORTUNITY</b>" if not test_only else "🧪 <b>LEARNER REJECT REPORT TEST — PASS PATH</b>",
        f"Asset: <code>solana:{html.escape(_short(mint, 34))}</code>",
        f"Reason: <code>{html.escape(reason[:700])}</code>",
    ]
    if wallet:
        lines.append(f"Leader: <code>{html.escape(_short(wallet))}</code>")
    if signature:
        lines.append(f"Signal: <code>{html.escape(_short(signature))}</code>")
    lines.append("Decision: <b>NO BUY / NO BROADCAST</b>")
    if test_only:
        lines.append("Synthetic reporting test only — no market opportunity was evaluated and no trade was placed.")
    try:
        sent_count = send_message(
            app.telegram_bot_token,
            str(tid),
            "\n".join(lines),
            parse_mode="HTML",
            protect_content=True,
        )
    except Exception as exc:
        raise RuntimeError("telegram_send_failed " + _telegram_error_text(exc)) from None
    return int(sent_count or 0) > 0


def _report_reject_actions(app, event: dict, actions: list[dict]) -> None:
    now = time.time()
    for key, ts in list(_REJECT_REPORT_DEDUP.items()):
        if now - ts > 900:
            _REJECT_REPORT_DEDUP.pop(key, None)
    published: set[tuple[str, str]] = set()
    for raw in actions or []:
        action = dict(raw or {})
        if str(action.get("action") or "").upper() != "REJECT":
            continue
        reason = str(action.get("reason") or "unspecified rejection")
        mint = str(event.get("mint") or action.get("mint") or "")
        event_id = str(event.get("event_id") or event.get("signature") or action.get("signature") or "")
        klass = _reject_class(action)
        pub_key = (klass, reason)
        if pub_key not in published:
            publish_rejection(
                chain="solana",
                token_address=mint,
                source="learnerbot",
                source_strategy_id=str(event.get("strategy_id") or "leader-copy"),
                source_event_id=event_id,
                rejection_class=klass,
                rejection_reason=reason,
                priority=75 if action.get("pool_risk_code") else 60,
                payload={
                    "risk_class": klass,
                    "leader_wallet": str(event.get("leader_wallet") or action.get("leader_wallet") or ""),
                    "source_runtime": "isolated_learner_solana",
                },
                require_market_reason=True,
            )
            published.add(pub_key)
        targets = _reject_targets(app, event, action)
        sent = 0
        for tid in targets:
            dedup_key = (str(tid), event_id or mint, reason)
            if dedup_key in _REJECT_REPORT_DEDUP and now - _REJECT_REPORT_DEDUP[dedup_key] < 300:
                continue
            try:
                if _send_reject_report(app, tid, event, action):
                    sent += 1
                    _REJECT_REPORT_DEDUP[dedup_key] = now
            except Exception as exc:
                print("[learner-reject-report] telegram_error=%s" % str(exc)[:220])
        print(
            "[learner-reject-report] decision=REJECT targets=%d sent=%d class=%s reason=%s" %
            (len(targets), sent, klass, reason[:180])
        )


'''
    changed |= replace_once(path, helper_anchor, helper, "def _report_reject_actions(app, event: dict, actions: list[dict])")

    action_anchor = """                    actions = _sol.process_leader_event(app, payload) or []\n                    if any(\n"""
    action_replacement = """                    actions = _sol.process_leader_event(app, payload) or []\n                    _report_reject_actions(app, payload, actions)\n                    if any(\n"""
    changed |= replace_once(path, action_anchor, action_replacement, "_report_reject_actions(app, payload, actions)")

    # This is deliberately last: older deployments already contain the markers above,
    # so they must still be migrated from the obsolete message-object return contract.
    changed |= _upgrade_existing_sender(path)
    return changed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--root", required=True)
    args = p.parse_args()
    repo = Path(args.repo).resolve()
    root = Path(args.root).resolve()
    results = {
        "publisher": install_publisher(repo, root),
        "outbound_telegram": patch_outbound_telegram(root),
        "final_reject_consumer": patch_final_reject_consumer(root),
    }
    print("learner_reject_reporting_patch=" + repr(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

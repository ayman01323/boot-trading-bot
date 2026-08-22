from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from .ai_council_http_patch import call_provider
from .telegram import send_to_chats
from scripts.ai_agent_ws_worker import _PROVIDER_CALL_LOCK

ADVISERS = ("claude", "gemini", "deepseek", "copilot")
MAX_REQUEST_CHARS = 3500
BRIDGE_PATH = Path("/var/tmp/boot/master_change_council_latest.json")
DEFAULT_GPT_MODEL = "gpt-5.6-terra"
_BUS_URL = "ws://127.0.0.1:8765"

_QUEUE: queue.Queue[tuple[Any, str]] = queue.Queue()
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False
_RUNNING: set[str] = set()

_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)(?:private[_ -]?key|mnemonic|seed phrase)\s*[:=]\s*\S.{8,}"),
)

_HARD_PROTECTED_TERMS = (
    "private key",
    "seed phrase",
    "mnemonic",
    "wallet signing",
    "signing key",
    "withdraw",
    "withdrawal",
    "transfer funds",
    "send funds",
    "api key",
    "secret token",
)

_PROTECTED_TERMS = (
    " live ",
    "armed",
    "capital",
    "risk limit",
    "risk threshold",
    "stop loss",
    "stop-loss",
    "slippage",
    "trade execution",
    "trading execution",
    "wallet",
    "signing",
    "deploy",
    "deployment",
    "sudo",
    " root ",
    "workflow",
    "github action",
)


def _state_dir(app) -> Path:
    path = Path(app.data_dir) / "master_change_council"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(app, request_id: str) -> Path:
    return _state_dir(app) / f"{request_id}.json"


def _latest_path(app) -> Path:
    return _state_dir(app) / "latest.json"


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _write_state(app, state: dict) -> dict:
    state["updated_epoch"] = int(time.time())
    _atomic_json(_state_path(app, str(state["request_id"])), state)
    _atomic_json(_latest_path(app), state)
    try:
        _atomic_json(BRIDGE_PATH, state)
        os.chmod(BRIDGE_PATH, 0o644)
    except Exception as exc:
        print(f"[master-change-bridge] {type(exc).__name__}: {exc}")
    return state


def load_request(app, request_id: str) -> dict:
    try:
        value = json.loads(_state_path(app, request_id).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def latest(app) -> dict:
    try:
        value = json.loads(_latest_path(app).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _source_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", "HEAD"],
            text=True,
            timeout=8,
        ).strip()
    except Exception:
        return ""


def _clean_request(value: str) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        raise ValueError("change request cannot be empty")
    if len(text) > MAX_REQUEST_CHARS:
        raise ValueError(f"change request is too long; maximum is {MAX_REQUEST_CHARS} characters")
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(text):
            raise ValueError("do not put credentials, private keys, seed phrases or bearer tokens in an AI change request")
    return text


def protection_reasons(text: str) -> tuple[list[str], list[str]]:
    lowered = " " + re.sub(r"\s+", " ", str(text or "").lower()) + " "
    hard = sorted({term.strip() for term in _HARD_PROTECTED_TERMS if term in lowered})
    protected = sorted({term.strip() for term in _PROTECTED_TERMS if term in lowered})
    return hard, protected


def _new_request_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"mc-{stamp}-{secrets.token_hex(3)}"


def _notify(app, chat_id: str | int, text: str) -> None:
    try:
        if getattr(app, "telegram_bot_token", "") and str(chat_id or ""):
            send_to_chats(app.telegram_bot_token, [str(chat_id)], text, disable_notification=False)
    except Exception as exc:
        print(f"[master-change-notify] {type(exc).__name__}: {exc}")


def _adviser_prompt(request_id: str, request: str, source_sha: str, adviser: str) -> str:
    return f"""You are {adviser.upper()}, one independent adviser in the MASTER Telegram Change Council.

A verified MASTER Telegram user has requested a repository change. You are advisory only: do not edit files, run shell/Git/GitHub commands, deploy, trade, sign transactions, change LIVE/ARMED/capital settings, or access secrets.

Request ID: {request_id}
Source SHA: {source_sha}
MASTER REQUEST:
{request}

Think independently. Identify whether the request is technically sound, hidden risks, likely files/components, tests/evidence required, and any safer alternative. Do not vote by personality or assume another agent agrees.

Return a concise recommendation for GPT. Explicitly state one of APPROVE, REJECT, or CLARIFY, with the reasoning and material risks.
"""


async def _ask_one(target: str, request_id: str, body: str, attempt: int, timeout: float = 150.0) -> dict:
    url = os.environ.get("AI_AGENT_BUS_URL", _BUS_URL)
    token = os.environ.get("AI_AGENT_BUS_TOKEN", "")
    message_id = f"master-change-{request_id}-{target}-{attempt}-{secrets.token_hex(2)}"
    result = {
        "target": target,
        "message_id": message_id,
        "acknowledged": False,
        "provider_rc": None,
        "reply": "",
        "error": "",
    }
    async with connect(url, ping_interval=20, ping_timeout=20, max_size=32_768) as ws:
        await ws.send(json.dumps({"type": "register", "agent": "gpt", "token": token}, separators=(",", ":")))
        registered = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if registered.get("type") != "registered":
            raise RuntimeError(f"bus registration failed: {registered}")
        await ws.send(json.dumps({
            "type": "send",
            "message_id": message_id,
            "from": "gpt",
            "to": target,
            "body": body,
        }, separators=(",", ":"), ensure_ascii=False))
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                result["error"] = "timeout waiting for adviser reply"
                return result
            data = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if data.get("message_id") != message_id:
                continue
            if data.get("type") == "status" and str(data.get("status") or "").upper() in {"ACKNOWLEDGED", "REPLIED"}:
                result["acknowledged"] = True
                continue
            if data.get("type") == "reply":
                result["acknowledged"] = True
                result["reply"] = str(data.get("body") or "")[:12000]
                result["error"] = str(data.get("error") or "")[:1200]
                # The broker currently preserves provider_rc inside the worker reply body/status path only indirectly.
                # A non-empty worker error is therefore the fail-closed provider signal.
                result["provider_rc"] = 0 if not result["error"] else 1
                return result
            if data.get("type") == "error":
                result["error"] = str(data.get("error") or "bus error")[:1200]
                return result


def _ask_adviser(target: str, request_id: str, request: str, source_sha: str, attempt: int) -> dict:
    try:
        return asyncio.run(_ask_one(target, request_id, _adviser_prompt(request_id, request, source_sha, target), attempt))
    except Exception as exc:
        return {
            "target": target,
            "message_id": "",
            "acknowledged": False,
            "provider_rc": 127,
            "reply": "",
            "error": f"{type(exc).__name__}: {exc}"[:1200],
        }


def _extract_json(text: str) -> dict:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("GPT decision did not contain JSON")
        value = json.loads(raw[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("GPT decision must be a JSON object")
    return value


def _gpt_decision_prompt(state: dict) -> str:
    evidence = []
    for adviser in ADVISERS:
        row = (state.get("advisers") or {}).get(adviser) or {}
        evidence.append(
            f"===== {adviser.upper()} =====\n"
            f"ACK: {bool(row.get('acknowledged'))}\n"
            f"ERROR: {row.get('error') or ''}\n"
            f"REPLY:\n{str(row.get('reply') or '')[:9000]}"
        )
    return f"""You are GPT, final adjudicator for a MASTER-requested repository change.

First analyse the MASTER request independently. Then critically evaluate every adviser report below. Do not decide by majority vote. Reject unsupported assumptions and preserve useful minority objections.

MASTER REQUEST ID: {state.get('request_id')}
SOURCE SHA: {state.get('source_sha')}
MASTER REQUEST:
{state.get('request')}

DETERMINISTIC HARD-PROTECTION SIGNALS: {state.get('hard_protected_reasons') or []}
DETERMINISTIC PROTECTED/RISK SIGNALS: {state.get('protected_reasons') or []}

ADVISER REPORTS:
{chr(10).join(evidence)}

Return ONLY one JSON object with this schema:
{{
  "action":"IMPLEMENT|REJECT|HUMAN_REVIEW",
  "risk_class":"LOW|MEDIUM|HIGH|CRITICAL",
  "summary":"short operator summary",
  "reasoning":"why this decision is justified",
  "allowed_files":["exact/repository/path.py"],
  "required_tests":["specific test command or test name"],
  "auto_merge_recommended":false
}}

Rules:
- IMPLEMENT means GPT believes a bounded code change should be attempted.
- allowed_files must be exact repository paths, not globs, and no more than 20 paths.
- Never put secrets, private keys, seed phrases or credentials in any field.
- HARD-protected requests should normally be HUMAN_REVIEW or REJECT; this council is not a wallet/signing/secrets authority.
- Trading/LIVE/risk/deployment changes may be IMPLEMENT only as a draft-PR change; never recommend automatic live deployment.
- auto_merge_recommended may be true only for clearly LOW-risk presentation/reporting/tests/docs changes.
"""


def _call_final_gpt(state: dict) -> tuple[int, str, str]:
    model = str(os.environ.get("AI_MASTER_CHANGE_GPT_MODEL") or DEFAULT_GPT_MODEL).strip()
    with _PROVIDER_CALL_LOCK:
        previous = os.environ.get("OPENAI_COUNCIL_MODEL")
        os.environ["OPENAI_COUNCIL_MODEL"] = model
        try:
            return call_provider("gpt", _gpt_decision_prompt(state))
        finally:
            if previous is None:
                os.environ.pop("OPENAI_COUNCIL_MODEL", None)
            else:
                os.environ["OPENAI_COUNCIL_MODEL"] = previous


def _normalise_decision(raw: dict) -> dict:
    action = str(raw.get("action") or "HUMAN_REVIEW").upper().strip()
    if action not in {"IMPLEMENT", "REJECT", "HUMAN_REVIEW"}:
        action = "HUMAN_REVIEW"
    risk = str(raw.get("risk_class") or "HIGH").upper().strip()
    if risk not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        risk = "HIGH"
    files = []
    for value in raw.get("allowed_files") or []:
        path = str(value or "").replace("\\", "/").strip().lstrip("/")
        if not path or "*" in path or ".." in Path(path).parts or path in files:
            continue
        files.append(path[:300])
        if len(files) >= 20:
            break
    tests = [str(x or "").strip()[:300] for x in (raw.get("required_tests") or []) if str(x or "").strip()][:12]
    return {
        "action": action,
        "risk_class": risk,
        "summary": str(raw.get("summary") or "")[:900],
        "reasoning": str(raw.get("reasoning") or "")[:3000],
        "allowed_files": files,
        "required_tests": tests,
        "auto_merge_recommended": bool(raw.get("auto_merge_recommended")),
    }


def _process(app, request_id: str) -> None:
    try:
        state = load_request(app, request_id)
        if not state:
            return
        state["status"] = "REVIEWING"
        state["attempt"] = int(state.get("attempt") or 0) + 1
        state["advisers"] = {}
        _write_state(app, state)
        _notify(app, state.get("requester_chat_id"), f"🧠 AI CHANGE {request_id}\nAll four adviser agents are reviewing your request. GPT will decide after their replies.")

        for adviser in ADVISERS:
            result = _ask_adviser(adviser, request_id, str(state.get("request") or ""), str(state.get("source_sha") or ""), int(state["attempt"]))
            state["advisers"][adviser] = result
            _write_state(app, state)

        all_ok = all(
            bool((state["advisers"].get(name) or {}).get("acknowledged"))
            and int((state["advisers"].get(name) or {}).get("provider_rc") or 1) == 0
            and bool(str((state["advisers"].get(name) or {}).get("reply") or "").strip())
            for name in ADVISERS
        )
        state["all_advisers_replied"] = all_ok
        if not all_ok:
            state["status"] = "INCOMPLETE"
            state["implementation_allowed"] = False
            _write_state(app, state)
            failed = [name for name in ADVISERS if int((state["advisers"].get(name) or {}).get("provider_rc") or 1) != 0]
            _notify(app, state.get("requester_chat_id"), f"⚠️ AI CHANGE {request_id}\nCouncil incomplete: {', '.join(failed) or 'one or more advisers'}. GPT implementation is blocked. Use /aichange retry {request_id}.")
            return

        rc, out, err = _call_final_gpt(state)
        if rc != 0 or not str(out or "").strip():
            state["status"] = "GPT_FAILED"
            state["gpt_error"] = str(err or f"GPT rc={rc}")[:1200]
            state["implementation_allowed"] = False
            _write_state(app, state)
            _notify(app, state.get("requester_chat_id"), f"⚠️ AI CHANGE {request_id}\nAll advisers replied, but GPT final decision failed. Use /aichange retry {request_id}.")
            return

        decision = _normalise_decision(_extract_json(out))
        state["gpt_decision"] = decision
        hard = list(state.get("hard_protected_reasons") or [])
        implementation_allowed = (
            decision["action"] == "IMPLEMENT"
            and bool(decision["allowed_files"])
            and not hard
        )
        state["implementation_allowed"] = implementation_allowed
        state["auto_merge_allowed"] = bool(
            implementation_allowed
            and decision["risk_class"] == "LOW"
            and decision["auto_merge_recommended"]
            and not state.get("protected_reasons")
        )
        state["implementation_nonce"] = int(state.get("implementation_nonce") or 0) + (1 if implementation_allowed else 0)
        state["bridge_revision"] = int(state.get("bridge_revision") or 0) + 1
        if implementation_allowed:
            state["status"] = "READY_FOR_IMPLEMENTATION"
        elif decision["action"] == "REJECT":
            state["status"] = "REJECTED"
        else:
            state["status"] = "HUMAN_REVIEW"
        _write_state(app, state)

        summary = decision.get("summary") or decision.get("reasoning") or "No summary"
        if implementation_allowed:
            next_step = "GPT implementation is authorised for a bounded branch/PR."
            if state.get("auto_merge_allowed"):
                next_step += " LOW-risk auto-merge is eligible after tests and path gates."
            else:
                next_step += " Automatic merge is blocked; the result will remain a draft PR."
        else:
            next_step = "No repository implementation will run from this request."
        _notify(
            app,
            state.get("requester_chat_id"),
            f"🧠 GPT MASTER DECISION — {request_id}\n"
            f"Decision: {decision['action']}\nRisk: {decision['risk_class']}\n"
            f"{summary[:1000]}\n\n{next_step}",
        )
    except Exception as exc:
        state = load_request(app, request_id)
        if state:
            state["status"] = "FAILED"
            state["error"] = f"{type(exc).__name__}: {exc}"[:1200]
            state["implementation_allowed"] = False
            _write_state(app, state)
            _notify(app, state.get("requester_chat_id"), f"⚠️ AI CHANGE {request_id} failed: {type(exc).__name__}: {exc}")
    finally:
        with _WORKER_LOCK:
            _RUNNING.discard(request_id)


def _worker_loop(app) -> None:
    while True:
        _, request_id = _QUEUE.get()
        try:
            _process(app, request_id)
        finally:
            _QUEUE.task_done()


def start_worker(app) -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        thread = threading.Thread(target=_worker_loop, args=(app,), name="master-change-council", daemon=True)
        thread.start()
        _WORKER_STARTED = True


def _enqueue(app, request_id: str) -> None:
    start_worker(app)
    with _WORKER_LOCK:
        if request_id in _RUNNING:
            return
        _RUNNING.add(request_id)
    _QUEUE.put((app, request_id))


def submit(app, requester_chat_id: str | int, request: str) -> dict:
    text = _clean_request(request)
    hard, protected = protection_reasons(text)
    request_id = _new_request_id()
    state = {
        "schema_version": 1,
        "request_id": request_id,
        "request": text,
        "requester_chat_id": str(requester_chat_id),
        "source_sha": _source_sha(),
        "created_epoch": int(time.time()),
        "updated_epoch": int(time.time()),
        "status": "QUEUED",
        "attempt": 0,
        "hard_protected_reasons": hard,
        "protected_reasons": protected,
        "advisers": {},
        "all_advisers_replied": False,
        "gpt_decision": {},
        "implementation_allowed": False,
        "auto_merge_allowed": False,
        "implementation_nonce": 0,
        "bridge_revision": 1,
    }
    _write_state(app, state)
    _enqueue(app, request_id)
    return state


def retry(app, request_id: str, requester_chat_id: str | int) -> dict:
    state = load_request(app, request_id)
    if not state:
        raise ValueError("AI change request not found")
    if str(state.get("requester_chat_id") or "") != str(requester_chat_id):
        # Another ACTIVE MASTER may inspect status, but only the originating MASTER
        # may re-spend provider calls for the same request.
        raise ValueError("only the MASTER who submitted this request may retry it")
    if str(state.get("status") or "") not in {"INCOMPLETE", "GPT_FAILED", "FAILED"}:
        raise ValueError("this request is not in a retryable state")
    state["status"] = "QUEUED"
    state["implementation_allowed"] = False
    state["bridge_revision"] = int(state.get("bridge_revision") or 0) + 1
    _write_state(app, state)
    _enqueue(app, request_id)
    return state


def status_text(state: dict) -> str:
    if not state:
        return "No MASTER AI change request exists yet."
    advisers = state.get("advisers") or {}
    rows = []
    for name in ADVISERS:
        row = advisers.get(name) or {}
        if row.get("provider_rc") == 0 and row.get("reply"):
            mark = "✅"
        elif row:
            mark = "⚠️"
        else:
            mark = "⏳"
        rows.append(f"{mark} {name.title()}")
    decision = state.get("gpt_decision") or {}
    lines = [
        f"🧠 AI CHANGE {state.get('request_id')}",
        f"Status: {state.get('status')}",
        f"Source: {str(state.get('source_sha') or '')[:12]}",
        *rows,
    ]
    if decision:
        lines += [
            f"GPT: {decision.get('action')} / {decision.get('risk_class')}",
            str(decision.get("summary") or "")[:900],
        ]
    if state.get("protected_reasons"):
        lines.append("Protected lane: automatic merge disabled.")
    if state.get("hard_protected_reasons"):
        lines.append("Hard-protected lane: implementation blocked; use the dedicated security/wallet workflow.")
    return "\n".join(x for x in lines if x)

from __future__ import annotations

import json
import os
import random
import re
import secrets
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROVIDERS = ("gpt", "gemini", "claude", "copilot", "deepseek")
LEADERS = PROVIDERS
MAX_QUESTION_CHARS = 6000
MAX_AGENT_ANSWER_CHARS = 12000
MAX_LEADER_INPUT_CHARS = 42000
_PROVIDER_TIMEOUT_SECONDS = 300
_GEMINI_DEFAULT_MODEL = "gemini-3.7-flash"
_GEMINI_MAX_ATTEMPTS = 4
_SESSION_RE = re.compile(r"^[0-9]{12}-[0-9a-f]{4}$")
_GEMINI_429_RE = re.compile(r"(?:HTTP\s*)?429|RESOURCE_EXHAUSTED|Too Many Requests", re.I)
_GEMINI_RETRY_RE = re.compile(r"(?:retry(?:Delay|\s+in)?)[\"'\s:=]+([0-9]+(?:\.[0-9]+)?)\s*s?", re.I)


class CouncilError(RuntimeError):
    pass


def _run(cmd: list[str], prompt: str, env: dict[str, str], *, stdin: bool = False) -> tuple[int, str, str]:
    try:
        cp = subprocess.run(
            cmd,
            input=prompt if stdin else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=_PROVIDER_TIMEOUT_SECONDS,
            check=False,
        )
        return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"provider timed out after {_PROVIDER_TIMEOUT_SECONDS}s"
    except Exception as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"


def _gemini_retry_seconds(out: str, err: str, attempt: int) -> float:
    text = f"{out}\n{err}"
    match = _GEMINI_RETRY_RE.search(text)
    if match:
        try:
            return max(1.0, min(float(match.group(1)), 60.0))
        except Exception:
            pass
    base = min(2 ** (attempt + 1), 30)
    return float(base) + random.uniform(0.0, 0.75)


def _run_gemini(cmd: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    last = (127, "", "Gemini call did not run")
    for attempt in range(_GEMINI_MAX_ATTEMPTS):
        last = _run(cmd, "", env)
        rc, out, err = last
        if rc == 0:
            return last
        if not _GEMINI_429_RE.search(f"{out}\n{err}"):
            return last
        if attempt + 1 >= _GEMINI_MAX_ATTEMPTS:
            return last
        time.sleep(_gemini_retry_seconds(out, err, attempt))
    return last


def call_provider(provider: str, prompt: str) -> tuple[int, str, str]:
    """Call one provider in read-only/non-interactive mode.

    The council is advisory only: provider harnesses receive no instruction to edit,
    deploy, trade, sign, transfer or mutate runtime state.
    """
    provider = str(provider or "").lower().strip()
    env = dict(os.environ)

    if provider == "gpt":
        key = str(env.get("OPENAI_API_KEY") or env.get("CODEX_API_KEY") or "").strip()
        if not key:
            return 90, "", "OPENAI_API_KEY missing"
        env["CODEX_API_KEY"] = key
        cmd = ["codex", "--ask-for-approval", "never", "exec", "--sandbox", "read-only", "--ephemeral", "-"]
        return _run(cmd, prompt, env, stdin=True)

    if provider == "gemini":
        if not str(env.get("GEMINI_API_KEY") or "").strip():
            return 90, "", "GEMINI_API_KEY missing"
        model = str(
            env.get("GEMINI_COUNCIL_MODEL")
            or env.get("GEMINI_MASTER_MODEL")
            or env.get("GEMINI_STRATEGY_MODEL")
            or _GEMINI_DEFAULT_MODEL
        ).strip()
        cmd = ["gemini", "--approval-mode=plan", "--skip-trust", "--output-format", "text", "--model", model, "-p", prompt]
        return _run_gemini(cmd, env)

    if provider == "claude":
        if not str(env.get("ANTHROPIC_API_KEY") or "").strip():
            return 90, "", "ANTHROPIC_API_KEY missing"
        model = str(env.get("CLAUDE_MASTER_MODEL") or "sonnet").strip()
        cmd = ["claude", "-p", "--permission-mode", "plan", "--max-turns", "1", "--output-format", "text", "--model", model, prompt]
        return _run(cmd, "", env)

    if provider == "copilot":
        token = str(
            env.get("COPILOT_GITHUB_TOKEN")
            or env.get("COPILOT_ASSIGN_TOKEN")
            or env.get("GH_TOKEN")
            or env.get("GITHUB_TOKEN")
            or ""
        ).strip()
        if not token:
            return 90, "", "Copilot token unavailable"
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token
        cmd = ["copilot", "--plan", "--no-auto-update", "--no-ask-user", "-s", "-p", prompt]
        return _run(cmd, "", env)

    if provider == "deepseek":
        key = str(env.get("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            return 90, "", "DEEPSEEK_API_KEY missing"
        model = str(env.get("DEEPSEEK_MASTER_MODEL") or "deepseek-v4-flash").strip()
        if not model.endswith("[1m]"):
            model = f"{model}[1m]"
        env.pop("ANTHROPIC_API_KEY", None)
        env["ANTHROPIC_BASE_URL"] = "https://api.deepseek.com/anthropic"
        env["ANTHROPIC_AUTH_TOKEN"] = key
        env["ANTHROPIC_MODEL"] = model
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = model
        env["DISABLE_AUTOUPDATER"] = "1"
        cmd = ["claude", "-p", "--permission-mode", "plan", "--max-turns", "1", "--output-format", "text", prompt]
        return _run(cmd, "", env)

    return 91, "", "unsupported provider"


def _session_dir(app) -> Path:
    path = Path(app.data_dir) / "ai_council"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(app, session_id: str) -> Path:
    session_id = str(session_id or "").strip()
    if not _SESSION_RE.fullmatch(session_id):
        raise CouncilError("invalid AI Council session id")
    return _session_dir(app) / f"{session_id}.json"


def _atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save_session(app, session: dict) -> dict:
    _atomic_json(_session_path(app, str(session.get("session_id") or "")), session)
    return session


def load_session(app, session_id: str) -> dict:
    path = _session_path(app, session_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CouncilError("AI Council session not found") from exc
    except Exception as exc:
        raise CouncilError(f"could not read AI Council session: {exc}") from exc
    if not isinstance(value, dict):
        raise CouncilError("invalid AI Council session data")
    return value


def create_session(app, chat_id: int | str, question: str, *, mode: str) -> dict:
    question = str(question or "").strip()
    if not question:
        raise CouncilError("question cannot be empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise CouncilError(f"question is too long; maximum is {MAX_QUESTION_CHARS} characters")
    now = int(time.time())
    session_id = time.strftime("%y%m%d%H%M%S", time.gmtime(now)) + "-" + secrets.token_hex(2)
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "chat_id": str(chat_id),
        "mode": str(mode or "user"),
        "question": question,
        "created_epoch": now,
        "updated_epoch": now,
        "status": "QUEUED",
        "providers": list(PROVIDERS),
        "answers": {},
        "leaders": {},
    }
    return save_session(app, session)


def _independent_prompt(question: str, provider: str) -> str:
    return f"""You are {provider.upper()}, one independent member of SiBot's AI Council.

Answer the user's question independently. You must not assume or predict what another AI agent will say. Be concise but substantive. Identify material uncertainty and unsupported assumptions. Where the question concerns this trading bot, treat the answer as advisory analysis only: do not edit files, deploy code, submit trades, sign transactions, transfer assets, alter LIVE/capital/wallet settings, or weaken deterministic safety gates.

USER QUESTION:
{question}

Return only your answer for the user. Do not add machine-readable wrappers.
"""


def _call_independent(provider: str, question: str) -> tuple[str, dict]:
    started = time.monotonic()
    rc, out, err = call_provider(provider, _independent_prompt(question, provider))
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if rc == 0 and out.strip():
        return provider, {
            "status": "DONE",
            "answer": out.strip()[:MAX_AGENT_ANSWER_CHARS],
            "error": "",
            "return_code": 0,
            "duration_ms": elapsed_ms,
        }
    return provider, {
        "status": "FAILED",
        "answer": "",
        "error": (err or f"provider exited {rc}")[:1200],
        "return_code": int(rc),
        "duration_ms": elapsed_ms,
    }


def run_independent_answers(app, session_id: str) -> dict:
    session = load_session(app, session_id)
    question = str(session.get("question") or "")
    session["status"] = "ASKING_AGENTS"
    session["updated_epoch"] = int(time.time())
    save_session(app, session)

    answers: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(PROVIDERS), thread_name_prefix="ai-council") as pool:
        futures = {pool.submit(_call_independent, provider, question): provider for provider in PROVIDERS}
        for future in as_completed(futures):
            provider = futures[future]
            try:
                name, result = future.result()
            except Exception as exc:
                name, result = provider, {
                    "status": "FAILED",
                    "answer": "",
                    "error": f"{type(exc).__name__}: {exc}"[:1200],
                    "return_code": 127,
                    "duration_ms": 0,
                }
            answers[name] = result

    session = load_session(app, session_id)
    session["answers"] = {p: answers.get(p, {"status": "FAILED", "answer": "", "error": "no result"}) for p in PROVIDERS}
    session["status"] = "ANSWERS_READY"
    session["updated_epoch"] = int(time.time())
    return save_session(app, session)


def _leader_prompt(session: dict, leader: str) -> str:
    question = str(session.get("question") or "")
    blocks: list[str] = []
    for provider in PROVIDERS:
        row = (session.get("answers") or {}).get(provider) or {}
        if str(row.get("status") or "") != "DONE" or not str(row.get("answer") or "").strip():
            continue
        blocks.append(f"===== {provider.upper()} ORIGINAL INDEPENDENT ANSWER =====\n{str(row.get('answer') or '')}")
    evidence = "\n\n".join(blocks)
    if len(evidence) > MAX_LEADER_INPUT_CHARS:
        evidence = evidence[:MAX_LEADER_INPUT_CHARS]
    return f"""You are {leader.upper()}, selected as SiBot AI Council Leader.

The user's original question and the original independent agent answers are supplied below. These original answers are immutable evidence. Critically examine them and produce one best consolidated answer.

Do NOT decide by majority vote. Identify important agreements and disagreements, reject factual errors or unsupported assumptions, preserve valuable minority views, state material uncertainty, and explain the reasoning behind the recommendation. Do not claim an unavailable agent agreed. Where the subject concerns the trading bot, this is advisory analysis only and cannot itself trade, deploy, sign, transfer assets, change LIVE/capital/wallet settings, or bypass deterministic safety gates.

Structure the response with these headings where useful:
FINAL ANSWER
KEY AGREEMENTS
IMPORTANT DISAGREEMENTS / RISKS
RECOMMENDED ACTION
CONFIDENCE

ORIGINAL USER QUESTION:
{question}

ORIGINAL INDEPENDENT ANSWERS:
{evidence or '[No valid independent answers were available.]'}

Return only the consolidated answer for the user.
"""


def run_leader(app, session_id: str, leader: str) -> dict:
    leader = str(leader or "").lower().strip()
    if leader not in LEADERS:
        raise CouncilError("unsupported AI Council leader")
    session = load_session(app, session_id)
    if str(session.get("status") or "") not in {"ANSWERS_READY", "LEADER_READY"}:
        raise CouncilError("independent AI answers are not ready yet")
    if not any(str((row or {}).get("status") or "") == "DONE" for row in (session.get("answers") or {}).values()):
        raise CouncilError("no independent AI answer completed successfully")

    started = time.monotonic()
    rc, out, err = call_provider(leader, _leader_prompt(session, leader))
    result = {
        "status": "DONE" if rc == 0 and out.strip() else "FAILED",
        "answer": out.strip()[:MAX_AGENT_ANSWER_CHARS] if rc == 0 else "",
        "error": "" if rc == 0 and out.strip() else (err or f"leader exited {rc}")[:1200],
        "return_code": int(rc),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "created_epoch": int(time.time()),
    }
    session = load_session(app, session_id)
    leaders = dict(session.get("leaders") or {})
    leaders[leader] = result
    session["leaders"] = leaders
    session["status"] = "LEADER_READY" if result["status"] == "DONE" else "ANSWERS_READY"
    session["updated_epoch"] = int(time.time())
    save_session(app, session)
    return result

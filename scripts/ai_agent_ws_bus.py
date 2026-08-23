from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

AGENTS = {"gpt", "claude", "gemini", "deepseek", "copilot"}
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_DB = "/var/tmp/boot/ai_agent_bus.sqlite3"
MAX_BODY_CHARS = 8000
PROGRESS_STATUSES = {"ACCEPTED", "EXECUTING"}
FINAL_STATUSES = {"REPLIED", "COMPLETED", "FAILED", "BLOCKED", "REJECTED"}


class BusError(ValueError):
    pass


def _now() -> int:
    return int(time.time())


def _normalise_agent(value: Any, *, allow_all: bool = False) -> str:
    agent = str(value or "").strip().lower()
    allowed = AGENTS | ({"all"} if allow_all else set())
    if agent not in allowed:
        raise BusError("unsupported agent")
    return agent


def _normalise_message_id(value: Any) -> str:
    message_id = str(value or "").strip()
    if not MESSAGE_ID_RE.fullmatch(message_id):
        raise BusError("invalid message_id")
    return message_id


def _normalise_body(value: Any) -> str:
    body = str(value or "").strip()
    if not body:
        raise BusError("message body cannot be empty")
    if len(body) > MAX_BODY_CHARS:
        raise BusError(f"message body exceeds {MAX_BODY_CHARS} characters")
    return body


def _normalise_progress(value: Any) -> str:
    status = str(value or "").strip().upper()
    if status not in PROGRESS_STATUSES:
        raise BusError("unsupported progress status")
    return status


def _normalise_final_status(value: Any) -> str:
    status = str(value or "REPLIED").strip().upper()
    if status not in FINAL_STATUSES:
        raise BusError("unsupported final status")
    return status


class Store:
    def __init__(self, path: str) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    target TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reply TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    delivered_at INTEGER,
                    acknowledged_at INTEGER,
                    replied_at INTEGER,
                    reply_delivered_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_messages_target_status
                    ON messages(target, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_messages_sender_reply_delivery
                    ON messages(sender, reply_delivered_at, replied_at);
                """
            )

    def put(self, message_id: str, sender: str, target: str, body: str) -> sqlite3.Row:
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
            if row is not None:
                if row["sender"] != sender or row["target"] != target or row["body"] != body:
                    raise BusError("message_id collision")
                return row
            conn.execute(
                """
                INSERT INTO messages
                    (message_id, sender, target, body, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'QUEUED', ?, ?)
                """,
                (message_id, sender, target, body, now, now),
            )
            return conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()

    def get(self, message_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()

    def pending_for_target(self, target: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute(
                """SELECT * FROM messages
                   WHERE target = ? AND status IN ('QUEUED', 'DELIVERED')
                   ORDER BY created_at ASC LIMIT 100""",
                (target,),
            ))

    def pending_replies_for_sender(self, sender: str) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in FINAL_STATUSES)
        params = [sender, *sorted(FINAL_STATUSES)]
        with self._connect() as conn:
            return list(conn.execute(
                f"""SELECT * FROM messages
                    WHERE sender = ? AND status IN ({placeholders})
                      AND replied_at IS NOT NULL AND reply_delivered_at IS NULL
                    ORDER BY replied_at ASC LIMIT 100""",
                params,
            ))

    def mark_delivered(self, message_id: str) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE messages
                   SET status = CASE WHEN status = 'QUEUED' THEN 'DELIVERED' ELSE status END,
                       delivered_at = COALESCE(delivered_at, ?), updated_at = ?
                   WHERE message_id = ?""",
                (now, now, message_id),
            )

    def acknowledge(self, message_id: str, target: str) -> sqlite3.Row:
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
            if row is None:
                raise BusError("unknown message_id")
            if row["target"] != target:
                raise BusError("only the addressed agent may acknowledge")
            if row["status"] in {"QUEUED", "DELIVERED"}:
                conn.execute(
                    """UPDATE messages
                       SET status = 'ACKNOWLEDGED',
                           acknowledged_at = COALESCE(acknowledged_at, ?),
                           updated_at = ?
                       WHERE message_id = ?""",
                    (now, now, message_id),
                )
            return conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()

    def progress(self, message_id: str, target: str, status: str) -> sqlite3.Row:
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
            if row is None:
                raise BusError("unknown message_id")
            if row["target"] != target:
                raise BusError("only the addressed agent may update progress")
            if row["status"] in FINAL_STATUSES:
                return row
            conn.execute(
                """UPDATE messages
                   SET status = ?, acknowledged_at = COALESCE(acknowledged_at, ?), updated_at = ?
                   WHERE message_id = ?""",
                (status, now, now, message_id),
            )
            return conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()

    def reply(
        self,
        message_id: str,
        target: str,
        body: str,
        *,
        error: str = "",
        final_status: str = "REPLIED",
    ) -> sqlite3.Row:
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
            if row is None:
                raise BusError("unknown message_id")
            if row["target"] != target:
                raise BusError("only the addressed agent may reply")
            conn.execute(
                """UPDATE messages
                   SET status = ?, reply = ?, error = ?, replied_at = ?, updated_at = ?
                   WHERE message_id = ?""",
                (final_status, body, str(error or "")[:1600], now, now, message_id),
            )
            return conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()

    def mark_reply_delivered(self, message_id: str) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE messages
                   SET reply_delivered_at = COALESCE(reply_delivered_at, ?), updated_at = ?
                   WHERE message_id = ?""",
                (now, now, message_id),
            )


class Broker:
    def __init__(self, store: Store, token: str = "") -> None:
        self.store = store
        self.token = str(token or "")
        self.connections: dict[str, set[ServerConnection]] = defaultdict(set)
        self.reverse: dict[ServerConnection, str] = {}
        self.lock = asyncio.Lock()

    async def _send(self, ws: ServerConnection, payload: dict[str, Any]) -> None:
        await ws.send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    async def _broadcast_agent(self, agent: str, payload: dict[str, Any]) -> bool:
        sent = False
        stale: list[ServerConnection] = []
        for ws in list(self.connections.get(agent, set())):
            try:
                await self._send(ws, payload)
                sent = True
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self._remove(ws)
        return sent

    async def _remove(self, ws: ServerConnection) -> None:
        async with self.lock:
            agent = self.reverse.pop(ws, None)
            if agent:
                self.connections[agent].discard(ws)
                if not self.connections[agent]:
                    self.connections.pop(agent, None)

    async def _register(self, ws: ServerConnection, data: dict[str, Any]) -> None:
        agent = _normalise_agent(data.get("agent"))
        if self.token and str(data.get("token") or "") != self.token:
            raise BusError("authentication failed")
        async with self.lock:
            old = self.reverse.get(ws)
            if old:
                self.connections[old].discard(ws)
            self.reverse[ws] = agent
            self.connections[agent].add(ws)
        await self._send(ws, {"type": "registered", "agent": agent})
        await self._deliver_pending(agent)
        await self._deliver_pending_replies(agent)

    def _message_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "type": "message",
            "message_id": row["message_id"],
            "from": row["sender"],
            "to": row["target"],
            "body": row["body"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    async def _deliver_pending(self, agent: str) -> None:
        if agent not in self.connections:
            return
        for row in self.store.pending_for_target(agent):
            if await self._broadcast_agent(agent, self._message_payload(row)):
                self.store.mark_delivered(row["message_id"])

    async def _deliver_pending_replies(self, agent: str) -> None:
        if agent not in self.connections:
            return
        for row in self.store.pending_replies_for_sender(agent):
            payload = {
                "type": "reply",
                "message_id": row["message_id"],
                "from": row["target"],
                "to": row["sender"],
                "body": row["reply"],
                "error": row["error"],
                "status": row["status"],
            }
            if await self._broadcast_agent(agent, payload):
                self.store.mark_reply_delivered(row["message_id"])

    async def _notify_sender_status(self, row: sqlite3.Row) -> None:
        await self._broadcast_agent(row["sender"], {
            "type": "status",
            "message_id": row["message_id"],
            "status": row["status"],
            "to": row["target"],
        })

    async def handle(self, ws: ServerConnection) -> None:
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise BusError("JSON object required")
                    kind = str(data.get("type") or "").strip().lower()
                    if kind == "register":
                        await self._register(ws, data)
                        continue
                    agent = self.reverse.get(ws)
                    if not agent:
                        raise BusError("register first")
                    if kind == "send":
                        await self._handle_send(ws, agent, data)
                    elif kind == "ack":
                        await self._handle_ack(agent, data)
                    elif kind == "progress":
                        await self._handle_progress(agent, data)
                    elif kind == "reply":
                        await self._handle_reply(agent, data)
                    elif kind == "status":
                        await self._handle_status(ws, agent, data)
                    elif kind == "ping":
                        await self._send(ws, {"type": "pong", "ts": _now()})
                    else:
                        raise BusError("unsupported message type")
                except BusError as exc:
                    await self._send(ws, {"type": "error", "error": str(exc)})
                except json.JSONDecodeError:
                    await self._send(ws, {"type": "error", "error": "invalid JSON"})
        finally:
            await self._remove(ws)

    async def _handle_send(self, ws: ServerConnection, agent: str, data: dict[str, Any]) -> None:
        message_id = _normalise_message_id(data.get("message_id"))
        target = _normalise_agent(data.get("to"), allow_all=True)
        body = _normalise_body(data.get("body"))
        if str(data.get("from") or agent).strip().lower() != agent:
            raise BusError("from does not match registered sender")
        if target == agent:
            raise BusError("sender cannot target itself")
        if target == "all":
            if os.environ.get("AI_AGENT_BUS_ALLOW_ALL", "0") != "1":
                raise BusError("broadcast disabled by default; send direct messages to avoid fan-out cost")
            targets = sorted(AGENTS - {agent})
            for recipient in targets:
                child_id = f"{message_id}:{recipient}"
                row = self.store.put(child_id, agent, recipient, body)
                if await self._broadcast_agent(recipient, self._message_payload(row)):
                    self.store.mark_delivered(child_id)
            await self._send(ws, {"type": "accepted", "message_id": message_id, "status": "FANOUT", "targets": targets})
            return
        row = self.store.put(message_id, agent, target, body)
        if await self._broadcast_agent(target, self._message_payload(row)):
            self.store.mark_delivered(message_id)
            row = self.store.get(message_id) or row
        await self._send(ws, {"type": "accepted", "message_id": message_id, "status": row["status"], "to": target})

    async def _handle_ack(self, agent: str, data: dict[str, Any]) -> None:
        row = self.store.acknowledge(_normalise_message_id(data.get("message_id")), agent)
        await self._notify_sender_status(row)

    async def _handle_progress(self, agent: str, data: dict[str, Any]) -> None:
        row = self.store.progress(
            _normalise_message_id(data.get("message_id")),
            agent,
            _normalise_progress(data.get("status")),
        )
        await self._notify_sender_status(row)

    async def _handle_reply(self, agent: str, data: dict[str, Any]) -> None:
        message_id = _normalise_message_id(data.get("message_id"))
        final_status = _normalise_final_status(data.get("status"))
        row = self.store.reply(
            message_id,
            agent,
            _normalise_body(data.get("body")),
            error=str(data.get("error") or ""),
            final_status=final_status,
        )
        payload = {
            "type": "reply",
            "message_id": row["message_id"],
            "from": row["target"],
            "to": row["sender"],
            "body": row["reply"],
            "error": row["error"],
            "status": row["status"],
        }
        if await self._broadcast_agent(row["sender"], payload):
            self.store.mark_reply_delivered(message_id)

    async def _handle_status(self, ws: ServerConnection, agent: str, data: dict[str, Any]) -> None:
        row = self.store.get(_normalise_message_id(data.get("message_id")))
        if row is None:
            raise BusError("unknown message_id")
        if agent not in {row["sender"], row["target"]}:
            raise BusError("status is visible only to the sender or recipient")
        await self._send(ws, {
            "type": "status",
            "message_id": row["message_id"],
            "from": row["sender"],
            "to": row["target"],
            "status": row["status"],
            "created_at": row["created_at"],
            "delivered_at": row["delivered_at"],
            "acknowledged_at": row["acknowledged_at"],
            "replied_at": row["replied_at"],
        })


async def run(host: str, port: int, db_path: str, token: str) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"} and not token:
        raise SystemExit("AI_AGENT_BUS_TOKEN is required when binding beyond loopback")
    broker = Broker(Store(db_path), token=token)
    async with serve(broker.handle, host, port, ping_interval=20, ping_timeout=20, max_size=32_768):
        print(f"ai-agent-ws-bus listening on ws://{host}:{port}", flush=True)
        await asyncio.Future()


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-cost local WebSocket AI agent message bus")
    parser.add_argument("--host", default=os.environ.get("AI_AGENT_BUS_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AI_AGENT_BUS_PORT", DEFAULT_PORT)))
    parser.add_argument("--db", default=os.environ.get("AI_AGENT_BUS_DB", DEFAULT_DB))
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port, args.db, os.environ.get("AI_AGENT_BUS_TOKEN", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

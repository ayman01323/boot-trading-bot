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
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    return True


def patch_pool_gate(root: Path) -> bool:
    path = root / "learnerbot" / "solana_pool_risk_gate.py"
    changed = False
    changed |= replace_once(
        path,
        "from . import solana_sibot as _sol\n",
        "from . import solana_sibot as _sol\nfrom .rejected_opportunity_publisher import publish_rejection\n",
        "from .rejected_opportunity_publisher import publish_rejection",
    )
    anchor = """    if _severity(result) > 0:\n        reason = f\"{result['reason_code']}: {result['reason']}\"\n        print(\"[solana-pool-risk] decision=%s code=%s mint=%s reason=%s\" % (\n"""
    replacement = """    if _severity(result) > 0:\n        reason = f\"{result['reason_code']}: {result['reason']}\"\n        event_id = str(\n            event.get(\"event_id\") or event.get(\"signature\") or event.get(\"tx_signature\")\n            or event.get(\"leader_signature\") or event.get(\"tx_hash\") or \"\"\n        )\n        publish_rejection(\n            chain=\"solana\",\n            token_address=str(event.get(\"mint\") or \"\"),\n            source=\"learnerbot\",\n            source_strategy_id=str(event.get(\"strategy_id\") or \"leader-copy\"),\n            source_event_id=event_id,\n            rejection_class=str(result.get(\"reason_code\") or \"POOL_RISK_REJECT\"),\n            rejection_reason=reason,\n            priority=75,\n            require_market_reason=False,\n            payload={\n                **dict(result.get(\"evidence\") or {}),\n                \"risk_class\": str(result.get(\"reason_code\") or \"POOL_RISK_REJECT\"),\n                \"poolcheck_decision\": str(result.get(\"decision\") or \"\"),\n                \"leader_wallet\": str(event.get(\"leader_wallet\") or \"\"),\n            },\n        )\n        print(\"[solana-pool-risk] decision=%s code=%s mint=%s reason=%s\" % (\n"""
    changed |= replace_once(path, anchor, replacement, "poolcheck_decision")
    return changed


def patch_sibot1_solana(root: Path) -> bool:
    path = root / "learnerbot" / "sibot1_solana_live_bridge_patch.py"
    changed = False
    changed |= replace_once(
        path,
        "from .user_registry import is_master, require_user\n",
        "from .user_registry import is_master, require_user\nfrom .rejected_opportunity_publisher import publish_rejection\n",
        "from .rejected_opportunity_publisher import publish_rejection",
    )
    old = """def _attempt_update(app, key, status, tx_signature=\"\", error=\"\") -> None:\n    with _DB_LOCK:\n        conn = _db(app)\n        try:\n            conn.execute(\n                \"UPDATE attempts SET status=?,tx_signature=?,error=?,updated_at=? WHERE attempt_key=?\",\n                (str(status), str(tx_signature or \"\"), str(error or \"\")[:1200], int(time.time()), str(key)),\n            )\n            conn.commit()\n        finally:\n            conn.close()\n"""
    new = """def _attempt_update(app, key, status, tx_signature=\"\", error=\"\") -> None:\n    row = None\n    now = int(time.time())\n    with _DB_LOCK:\n        conn = _db(app)\n        try:\n            conn.execute(\n                \"UPDATE attempts SET status=?,tx_signature=?,error=?,updated_at=? WHERE attempt_key=?\",\n                (str(status), str(tx_signature or \"\"), str(error or \"\")[:1200], now, str(key)),\n            )\n            row = conn.execute(\n                \"SELECT attempt_key,candidate_id,kind,engine_id,chain,mint,status,error,updated_at FROM attempts WHERE attempt_key=?\",\n                (str(key),),\n            ).fetchone()\n            conn.commit()\n        finally:\n            conn.close()\n    if row is not None and str(row[\"kind\"] or \"\").upper() == \"ENTRY\" and str(error or \"\"):\n        reason = str(error or \"\")\n        klass = reason.split(\":\", 1)[0].strip().upper().replace(\" \", \"_\")[:80] or \"MARKET_RISK_REJECT\"\n        publish_rejection(\n            chain=str(row[\"chain\"] or \"solana\"), token_address=str(row[\"mint\"] or \"\"),\n            source=str(row[\"engine_id\"] or \"sibot1\"), source_strategy_id=str(row[\"engine_id\"] or \"\"),\n            source_event_id=str(row[\"attempt_key\"] or row[\"candidate_id\"] or \"\"),\n            rejection_class=klass, rejection_reason=reason,\n            priority=80 if str(row[\"engine_id\"] or \"\").lower() in {\"gemini\", \"grok\"} else 65,\n            observed_at=int(row[\"updated_at\"] or now),\n            payload={\"risk_class\": klass, \"source_runtime\": \"sibot1_solana_live_bridge\",\n                     \"source_candidate_id\": str(row[\"candidate_id\"] or \"\"),\n                     \"source_status\": str(row[\"status\"] or \"\")},\n        )\n"""
    changed |= replace_once(path, old, new, "source_runtime\": \"sibot1_solana_live_bridge")
    return changed


def patch_sibot1_evm(root: Path) -> bool:
    path = root / "learnerbot" / "sibot1_live_bridge_patch.py"
    changed = False
    changed |= replace_once(
        path,
        "from .user_registry import is_master, require_user, user_bool\n",
        "from .user_registry import is_master, require_user, user_bool\nfrom .rejected_opportunity_publisher import publish_rejection\n",
        "from .rejected_opportunity_publisher import publish_rejection",
    )
    migrate_old = """    \"\"\")\n    return conn\n\n\ndef _attempt_key"""
    migrate_new = """    \"\"\")\n    cols = {str(r[1]) for r in conn.execute(\"PRAGMA table_info(attempts)\").fetchall()}\n    if \"engine_id\" not in cols:\n        conn.execute(\"ALTER TABLE attempts ADD COLUMN engine_id TEXT\")\n    if \"token\" not in cols:\n        conn.execute(\"ALTER TABLE attempts ADD COLUMN token TEXT\")\n    return conn\n\n\ndef _attempt_key"""
    changed |= replace_once(path, migrate_old, migrate_new, "ALTER TABLE attempts ADD COLUMN engine_id")

    claim_old = """def _claim(app, tid, candidate) -> tuple[bool, str]:\n    key = _attempt_key(tid, candidate)\n    now = int(time.time())\n    with _DB_LOCK:\n        conn = _db(app)\n        try:\n            cur = conn.execute(\n                \"INSERT OR IGNORE INTO attempts(attempt_key,telegram_id,candidate_id,kind,chain,shadow_lot_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)\",\n                (key, str(tid), str(candidate.get(\"candidate_id\") or \"\"), str(candidate.get(\"kind\") or \"\"), str(candidate.get(\"chain\") or \"\"), str(candidate.get(\"shadow_lot_id\") or \"\"), \"CLAIMED\", now, now),\n            )\n            conn.commit()\n            return cur.rowcount == 1, key\n        finally:\n            conn.close()\n"""
    claim_new = """def _claim(app, tid, candidate) -> tuple[bool, str]:\n    key = _attempt_key(tid, candidate)\n    now = int(time.time())\n    engine_id = str(candidate.get(\"engine_id\") or \"gpt\")\n    token = str(candidate.get(\"asset_out\") or candidate.get(\"asset\") or candidate.get(\"token\") or \"\")\n    with _DB_LOCK:\n        conn = _db(app)\n        try:\n            cur = conn.execute(\n                \"INSERT OR IGNORE INTO attempts(attempt_key,telegram_id,candidate_id,kind,chain,shadow_lot_id,status,created_at,updated_at,engine_id,token) VALUES(?,?,?,?,?,?,?,?,?,?,?)\",\n                (key, str(tid), str(candidate.get(\"candidate_id\") or \"\"), str(candidate.get(\"kind\") or \"\"), str(candidate.get(\"chain\") or \"\"), str(candidate.get(\"shadow_lot_id\") or \"\"), \"CLAIMED\", now, now, engine_id, token),\n            )\n            conn.commit()\n            return cur.rowcount == 1, key\n        finally:\n            conn.close()\n"""
    changed |= replace_once(path, claim_old, claim_new, "engine_id = str(candidate.get(\"engine_id\") or \"gpt\")")

    update_old = """def _attempt_update(app, key, status, tx_hash=\"\", error=\"\") -> None:\n    with _DB_LOCK:\n        conn = _db(app)\n        try:\n            conn.execute(\n                \"UPDATE attempts SET status=?,tx_hash=?,error=?,updated_at=? WHERE attempt_key=?\",\n                (str(status), str(tx_hash or \"\"), str(error or \"\")[:1200], int(time.time()), str(key)),\n            )\n            conn.commit()\n        finally:\n            conn.close()\n"""
    update_new = """def _attempt_update(app, key, status, tx_hash=\"\", error=\"\") -> None:\n    row = None\n    now = int(time.time())\n    with _DB_LOCK:\n        conn = _db(app)\n        try:\n            conn.execute(\n                \"UPDATE attempts SET status=?,tx_hash=?,error=?,updated_at=? WHERE attempt_key=?\",\n                (str(status), str(tx_hash or \"\"), str(error or \"\")[:1200], now, str(key)),\n            )\n            row = conn.execute(\n                \"SELECT attempt_key,candidate_id,kind,chain,status,error,updated_at,engine_id,token FROM attempts WHERE attempt_key=?\",\n                (str(key),),\n            ).fetchone()\n            conn.commit()\n        finally:\n            conn.close()\n    if row is not None and str(row[\"kind\"] or \"\").upper() == \"ENTRY\" and str(error or \"\"):\n        reason = str(error or \"\")\n        klass = reason.split(\":\", 1)[0].strip().upper().replace(\" \", \"_\")[:80] or \"MARKET_RISK_REJECT\"\n        publish_rejection(\n            chain=str(row[\"chain\"] or \"base\"), token_address=str(row[\"token\"] or \"\"),\n            source=str(row[\"engine_id\"] or \"gpt\"), source_strategy_id=str(row[\"engine_id\"] or \"gpt\"),\n            source_event_id=str(row[\"attempt_key\"] or row[\"candidate_id\"] or \"\"),\n            rejection_class=klass, rejection_reason=reason, priority=60,\n            observed_at=int(row[\"updated_at\"] or now),\n            payload={\"risk_class\": klass, \"source_runtime\": \"sibot1_evm_live_bridge\",\n                     \"source_candidate_id\": str(row[\"candidate_id\"] or \"\"),\n                     \"source_status\": str(row[\"status\"] or \"\")},\n        )\n"""
    changed |= replace_once(path, update_old, update_new, "source_runtime\": \"sibot1_evm_live_bridge")
    return changed


def patch_sirisky(root: Path) -> bool:
    path = root / "sirisky" / "engine.py"
    changed = False
    changed |= replace_once(
        path,
        "from .stage8_review import Stage8Review\n",
        "from .stage8_review import Stage8Review\nfrom .rejected_queue import RejectedOpportunityConsumer\n",
        "from .rejected_queue import RejectedOpportunityConsumer",
    )
    changed |= replace_once(
        path,
        "        self.approvals=ManualApprovalGate(self.settings)\n",
        "        self.approvals=ManualApprovalGate(self.settings)\n        self.rejected_queue=RejectedOpportunityConsumer(self.settings)\n",
        "self.rejected_queue=RejectedOpportunityConsumer(self.settings)",
    )
    anchor = """        pools=self.settings.selected_pools()\n        auto_mode=False\n"""
    replacement = """        if not self.pending_approvals():\n            claimed = self.rejected_queue.claim_pool()\n            if claimed:\n                queue_item, queue_pool = claimed\n                result = self._evaluate_pool_for_entry(\n                    queue_pool,\n                    {\"status\": \"REJECTED_OPPORTUNITY_QUEUE\", \"count\": 1, \"updated\": False},\n                )\n                return self.rejected_queue.finish(queue_item, queue_pool, result)\n\n        pools=self.settings.selected_pools()\n        auto_mode=False\n"""
    changed |= replace_once(path, anchor, replacement, "REJECTED_OPPORTUNITY_QUEUE")
    return changed


def install_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--main-root")
    p.add_argument("--claude-root")
    p.add_argument("--sirisky-root")
    args = p.parse_args()
    repo = Path(args.repo).resolve()
    results = {}

    if args.main_root:
        root = Path(args.main_root)
        install_file(repo / "learnerbot" / "rejected_opportunity_publisher.py", root / "learnerbot" / "rejected_opportunity_publisher.py")
        results["main_pool"] = patch_pool_gate(root)
        results["sibot1_solana"] = patch_sibot1_solana(root)
        results["sibot1_evm"] = patch_sibot1_evm(root)

    if args.claude_root:
        root = Path(args.claude_root)
        install_file(repo / "learnerbot" / "rejected_opportunity_publisher.py", root / "learnerbot" / "rejected_opportunity_publisher.py")
        results["claude_pool"] = patch_pool_gate(root)

    if args.sirisky_root:
        root = Path(args.sirisky_root)
        install_file(repo / "SiRisky" / "overrides" / "sirisky" / "rejected_queue.py", root / "sirisky" / "rejected_queue.py")
        results["sirisky_consumer"] = patch_sirisky(root)

    print("integration_patch_results=" + repr(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

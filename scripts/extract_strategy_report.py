from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from learnerbot.three_agent_strategy_contract import validate_agent_report


def extract(text: str) -> dict:
    marker = re.search(r"STRATEGY_REVIEW_JSON_BEGIN\s*(\{.*?\})\s*STRATEGY_REVIEW_JSON_END", text, re.S)
    if marker:
        value = json.loads(marker.group(1))
        if isinstance(value, dict):
            return value
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fenced:
        value = json.loads(fenced.group(1))
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"\{", text))):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    raise ValueError("no strategy review JSON object found")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--provider", required=True)
    p.add_argument("--cycle-id", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--evidence-sha256", required=True)
    args = p.parse_args()
    payload = extract(Path(args.input).read_text(encoding="utf-8", errors="replace"))
    validate_agent_report(
        payload,
        provider=args.provider,
        cycle_id=args.cycle_id,
        source_commit=args.source_commit,
        evidence_sha256=args.evidence_sha256,
    )
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

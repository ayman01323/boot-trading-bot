from __future__ import annotations

import argparse
from pathlib import Path

APPROVED = "2026-08-29T11:05:51Z"


def _extract_tested_reporting(text: str) -> str:
    start = text.find("_REJECT_REPORT_SUPPRESS_SECONDS = 15 * 60")
    end = text.find("\ndef _leader_signatures", start)
    if start < 0 or end < 0:
        raise RuntimeError("tested Change Set 5 reporting anchors missing")
    block = text[start:end]
    block += (
        '\nprint("[learner-reject-changeset5] approved=2026-08-29T11:05:51Z '
        'dedup_seconds=900 full_clickable_ids=true reporting_only=true")\n'
    )
    return block


def _current_reporting_range(text: str) -> tuple[int, int]:
    if "_REJECT_REPORT_SUPPRESS_SECONDS = 15 * 60" in text:
        start = text.find("_REJECT_REPORT_SUPPRESS_SECONDS = 15 * 60")
    else:
        start = text.find("_REJECT_REPORT_DEDUP: dict[tuple[str, str, str], float] = {}")
    end = text.find("\ndef _leader_signatures", start)
    if start < 0 or end < 0:
        raise RuntimeError("current Learner reporting anchors missing")
    return start, end


def apply(target: Path, tested_source: Path) -> None:
    old = target.read_text(encoding="utf-8")
    tested = tested_source.read_text(encoding="utf-8")

    required_imports = (
        "import html",
        "from .rejected_opportunity_publisher import publish_rejection",
        "from .solana_live_patch import live_enabled",
        "from .telegram import send_message",
        "from .user_registry import all_users",
    )
    missing = [item for item in required_imports if item not in old]
    if missing:
        raise RuntimeError("current runtime lacks reporting imports: " + ", ".join(missing))

    reporting = _extract_tested_reporting(tested)
    start, end = _current_reporting_range(old)
    prefix, suffix = old[:start], old[end:]
    new = prefix + reporting + suffix

    # Compile in memory so verification never depends on __pycache__ permissions.
    compile(new, str(target), "exec")

    if not new.startswith(prefix) or not new.endswith(suffix):
        raise RuntimeError("non-reporting source region changed")
    if "_REJECT_REPORT_SUPPRESS_SECONDS = 15 * 60" not in new:
        raise RuntimeError("900-second rejection suppression missing")
    if "def _telegram_reject_dedup_key" not in new:
        raise RuntimeError("condition-level Telegram dedupe missing")
    for marker in (
        "https://solscan.io/token/",
        "https://solscan.io/account/",
        "https://solscan.io/tx/",
        "https://www.dexview.com/solana/",
    ):
        if marker not in new:
            raise RuntimeError(f"clickable link marker missing: {marker}")
    if "_short(wallet)" in new or "_short(signature)" in new:
        raise RuntimeError("legacy truncated Leader/Signal formatter remains")

    target.write_text(new, encoding="utf-8")
    print("changeset5_reporting_section_replaced=true")
    print("changeset5_non_reporting_prefix_unchanged=true")
    print("changeset5_non_reporting_suffix_unchanged=true")
    print("changeset5_in_memory_compile=true")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--tested-source", required=True)
    args = parser.parse_args()
    apply(Path(args.target).resolve(), Path(args.tested_source).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

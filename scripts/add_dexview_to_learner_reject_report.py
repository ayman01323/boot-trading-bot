from __future__ import annotations

import argparse
from pathlib import Path


def patch(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    marker = "https://www.dexview.com/solana/"
    if marker in text:
        return False
    anchor = '        f"Reason: <code>{html.escape(reason[:700])}</code>",\n    ]\n'
    replacement = (
        '        f"Reason: <code>{html.escape(reason[:700])}</code>",\n'
        '        f"Dexview: https://www.dexview.com/solana/{html.escape(mint, quote=True)}",\n'
        '    ]\n'
    )
    if anchor not in text:
        raise RuntimeError("Learner reject report format anchor not found")
    path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    target = Path(args.root).resolve() / "learnerbot" / "solana_leader_cursor_reliability_patch.py"
    changed = patch(target)
    print(f"learner_reject_dexview_patch={'changed' if changed else 'already_present'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

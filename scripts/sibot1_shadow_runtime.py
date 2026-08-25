#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The exporter observes only paper-filled SiBot 1 intents and writes sanitized
# candidate records for the separate protected execution bridge. It does not
# attach a signer or change the SHADOW/PAPER runtime boundary.
from sibot1_engines._shared import live_candidate_export as _live_candidate_export  # noqa: F401,E402
# GPT atomic Base cycles are exported only after central PoolCheck PASS and
# successful paper entry+exit accounting. Cross-DEX GPT research remains
# paper-only unless a true atomic multi-venue executor exists.
from sibot1_engines._shared import live_atomic_cycle_export as _live_atomic_cycle_export  # noqa: F401,E402
from sibot1_engines._shared.runtime import run_shadow_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SiBot 1 independent engines in hard-disabled SHADOW/PAPER mode")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    run_shadow_runtime(Path(args.root).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sibot1_engines._shared.runtime import run_shadow_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SiBot 1 independent engines in hard-disabled SHADOW/PAPER mode")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    run_shadow_runtime(Path(args.root).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

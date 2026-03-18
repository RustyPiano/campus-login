#!/usr/bin/env python3
"""Compatibility wrapper for the historical single-file entrypoint."""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


legacy_main = importlib.import_module("campus_login_tool.cli").legacy_main


if __name__ == "__main__":
    raise SystemExit(legacy_main())

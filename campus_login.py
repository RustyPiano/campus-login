#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility wrapper for the historical single-file entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

from campus_login_tool.cli import legacy_main


if __name__ == "__main__":
    raise SystemExit(legacy_main())

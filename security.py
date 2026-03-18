#!/usr/bin/env python3
"""Compatibility shim for the historical root-level RSA helpers.

The maintained implementation lives in `campus_login_tool.security`.
"""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


_security = importlib.import_module("campus_login_tool.security")


__all__ = [name for name in vars(_security) if not name.startswith("_")]

globals().update({name: getattr(_security, name) for name in __all__})

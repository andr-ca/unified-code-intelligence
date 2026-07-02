"""``uci.cli`` — command-line entry point."""

from __future__ import annotations

from .main import build_parser, main

__all__ = ["main", "build_parser"]

"""``uci.api`` — REST API + server-rendered dashboard (stdlib HTTP server, zero runtime deps)."""

from __future__ import annotations

from .server import make_handler, serve

__all__ = ["serve", "make_handler"]

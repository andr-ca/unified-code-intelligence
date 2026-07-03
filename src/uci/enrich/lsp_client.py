"""Minimal LSP client over stdio — stdlib only, same transport philosophy as the MCP server.

Language Server Protocol is JSON-RPC 2.0 framed with ``Content-Length`` headers (unlike the MCP
server's newline-delimited framing). We implement only what edge enrichment needs — ``initialize``,
``didOpen``, ``documentSymbol``, ``definition``, ``references``, ``shutdown`` — in a synchronous
request/response style: send a request, then read messages until the matching id arrives, skipping
server notifications and server→client requests along the way. That is sufficient for batch
enrichment (docs/lsp-refactoring-recommendations.md §2.2); no third-party SDK is required.

The wire framing (:func:`encode_message` / :func:`read_message`) is separated from the process
wrapper so it can be unit-tested over in-memory byte streams without launching a real server.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, BinaryIO


class LspError(Exception):
    """A protocol-level failure (transport closed, server error response, bad framing)."""


def encode_message(obj: dict[str, Any]) -> bytes:
    """Serialize a JSON-RPC object with the LSP ``Content-Length`` header framing."""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def read_message(reader: BinaryIO) -> dict[str, Any] | None:
    """Read one framed JSON-RPC message from ``reader``. Returns ``None`` at clean end-of-stream.

    Raises :class:`LspError` on truncated framing (stream closed mid-message)."""
    headers: dict[str, str] = {}
    while True:
        line = reader.readline()
        if not line:  # EOF
            return None if not headers else _raise("stream closed before message body")
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii", errors="replace").strip()
        if ":" in text:
            key, _, value = text.partition(":")
            headers[key.strip().lower()] = value.strip()
    try:
        length = int(headers.get("content-length", ""))
    except ValueError:
        raise LspError(f"missing/invalid Content-Length header: {headers!r}")
    body = _read_exact(reader, length)
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LspError(f"invalid JSON body: {exc}")


def _read_exact(reader: BinaryIO, n: int) -> bytes:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = reader.read(remaining)
        if not chunk:
            raise LspError(f"stream closed with {remaining} of {n} body bytes unread")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _raise(msg: str):
    raise LspError(msg)


def path_to_uri(path: str | Path) -> str:
    """Turn a filesystem path into a ``file://`` URI the way language servers expect."""
    return Path(path).resolve().as_uri()


class LspClient:
    """Synchronous LSP client over a reader/writer byte-stream pair.

    Use :meth:`spawn` to launch a real server, or construct directly with in-memory streams in
    tests. ``request`` blocks until the response with the matching id is read; interleaved server
    notifications (e.g. ``textDocument/publishDiagnostics``) are collected in :attr:`notifications`
    and server→client requests are answered with a null result to keep the server unblocked.
    """

    def __init__(self, reader: BinaryIO, writer: BinaryIO, process: subprocess.Popen | None = None):
        self._reader = reader
        self._writer = writer
        self._process = process
        self._seq = 0
        self.notifications: list[dict[str, Any]] = []

    @classmethod
    def spawn(cls, cmd: list[str], cwd: str | None = None) -> "LspClient":
        """Launch a language server subprocess. Raises :class:`LspError` if the binary is absent."""
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0)
        except (FileNotFoundError, OSError) as exc:
            raise LspError(f"cannot launch language server {cmd!r}: {exc}")
        assert proc.stdin and proc.stdout
        return cls(proc.stdout, proc.stdin, process=proc)

    # -- low-level ----------------------------------------------------------
    def _send(self, obj: dict[str, Any]) -> None:
        try:
            self._writer.write(encode_message(obj))
            self._writer.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            raise LspError(f"failed to write to server: {exc}")

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._seq += 1
        req_id = self._seq
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        while True:
            msg = read_message(self._reader)
            if msg is None:
                raise LspError(f"server closed the connection during {method!r}")
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise LspError(f"{method} failed: {msg['error']}")
                return msg.get("result")
            if "method" in msg and "id" in msg:  # server→client request: answer null, stay unblocked
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
            elif "method" in msg:  # notification
                self.notifications.append(msg)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # -- high-level LSP verbs ----------------------------------------------
    def initialize(self, root_path: str, capabilities: dict[str, Any] | None = None,
                   initialization_options: dict[str, Any] | None = None) -> Any:
        params: dict[str, Any] = {
            "processId": None,
            "rootUri": path_to_uri(root_path),
            "capabilities": capabilities or {},
        }
        if initialization_options:
            params["initializationOptions"] = initialization_options
        result = self.request("initialize", params)
        self.notify("initialized", {})
        return result

    def did_open(self, path: str, language_id: str, text: str) -> None:
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": path_to_uri(path), "languageId": language_id, "version": 1, "text": text}})

    def document_symbol(self, path: str) -> Any:
        return self.request("textDocument/documentSymbol",
                            {"textDocument": {"uri": path_to_uri(path)}})

    def definition(self, path: str, line: int, character: int) -> Any:
        """``line``/``character`` are 0-based per the LSP spec (callers convert from 1-based)."""
        return self.request("textDocument/definition", {
            "textDocument": {"uri": path_to_uri(path)},
            "position": {"line": line, "character": character}})

    def references(self, path: str, line: int, character: int,
                   include_declaration: bool = False) -> Any:
        return self.request("textDocument/references", {
            "textDocument": {"uri": path_to_uri(path)},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration}})

    def shutdown(self) -> None:
        try:
            self.request("shutdown")
            self.notify("exit")
        except LspError:
            pass
        finally:
            if self._process is not None:
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()


__all__ = ["LspClient", "LspError", "encode_message", "read_message", "path_to_uri"]

#!/usr/bin/env python3
"""A scripted LSP server for evaluating UCI's edge-oracle bridge — no real toolchain required.

It speaks genuine LSP over stdio (Content-Length JSON-RPC framing) so `evals/lsp_eval.py` exercises
the *full* stack — `LspClient.spawn` → subprocess → framing → definition/references → apply — exactly
as a real language server would, but with deterministic, golden answers. This makes the LSP bridge
eval reproducible and CI-safe (it only needs Python).

Usage:  python scripted_lsp.py <responses.json> <repo_root>

responses.json:
  {
    "definitions": [ {"file": "MAIN.cbl", "line": 2, "target": "cbl/HELPER.cbl", "target_line": 0} ],
    "references":  [ {"file": "HELPER.cbl", "line": 0,
                      "targets": [ {"file": "cbl/MAIN.cbl", "line": 2} ]} ]
  }
`file` matches the request document's basename; `line` is the 0-based request position line; targets
are repo-relative paths turned into `file://<repo_root>/<target>` URIs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _read_message(reader) -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = reader.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii", "replace")
        if ":" in text:
            k, _, v = text.partition(":")
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0"))
    body = reader.read(length)
    return json.loads(body.decode("utf-8"))


def _write_message(writer, obj: dict) -> None:
    body = json.dumps(obj).encode("utf-8")
    writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    writer.flush()


def _uri(root: str, rel: str) -> str:
    return Path(root, rel).resolve().as_uri()


def _location(root: str, rel: str, line0: int) -> dict:
    return {"uri": _uri(root, rel),
            "range": {"start": {"line": line0, "character": 0},
                      "end": {"line": line0, "character": 1}}}


def main() -> int:
    responses = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = sys.argv[2]
    defs = responses.get("definitions", [])
    refs = responses.get("references", [])
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer

    while True:
        msg = _read_message(stdin)
        if msg is None:
            return 0
        method = msg.get("method")
        req_id = msg.get("id")
        if method == "initialize":
            _write_message(stdout, {"jsonrpc": "2.0", "id": req_id, "result": {
                "capabilities": {"definitionProvider": True, "referencesProvider": True},
                "serverInfo": {"name": "scripted-lsp", "version": "1.0"}}})
        elif method == "shutdown":
            _write_message(stdout, {"jsonrpc": "2.0", "id": req_id, "result": None})
        elif method == "exit":
            return 0
        elif method in ("textDocument/definition", "textDocument/references"):
            doc = Path(_uri_path(msg["params"]["textDocument"]["uri"])).name
            line = int(msg["params"]["position"]["line"])
            if method == "textDocument/definition":
                hit = next((d for d in defs if d["file"] == doc and int(d["line"]) == line), None)
                result = _location(root, hit["target"], int(hit.get("target_line", 0))) if hit else None
            else:
                hit = next((r for r in refs if r["file"] == doc and int(r["line"]) == line), None)
                result = [_location(root, t["file"], int(t.get("line", 0)))
                          for t in hit["targets"]] if hit else []
            _write_message(stdout, {"jsonrpc": "2.0", "id": req_id, "result": result})
        elif req_id is not None:  # unknown request → method-not-found, keep serving
            _write_message(stdout, {"jsonrpc": "2.0", "id": req_id,
                                    "error": {"code": -32601, "message": f"unhandled: {method}"}})
        # notifications (initialized, didOpen, …) need no response


def _uri_path(uri: str) -> str:
    from urllib.parse import unquote, urlparse
    return unquote(urlparse(uri).path)


if __name__ == "__main__":
    sys.exit(main())

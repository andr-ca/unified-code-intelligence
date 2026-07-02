"""Language detection and module-name derivation."""

from __future__ import annotations

_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "javascript", ".tsx": "javascript",
    ".cbl": "cobol", ".cob": "cobol", ".cpy": "cobol",
    ".jcl": "jcl", ".prc": "jcl", ".proc": "jcl",
    ".csd": "csd", ".bms": "bms",
    ".asm": "hlasm", ".hlasm": "hlasm",
    ".env": "config", ".ini": "config", ".cfg": "config", ".toml": "config",
    ".yaml": "config", ".yml": "config", ".json": "config", ".properties": "config",
}

_CODE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".cbl", ".cob", ".cpy", ".jcl", ".prc", ".proc", ".asm", ".hlasm",
})

# mainframe members live in flat libraries: the member name IS the module name
_MAINFRAME_EXTS = (".cbl", ".cob", ".cpy", ".jcl", ".prc", ".proc", ".csd", ".asm", ".hlasm")

# extensions we treat as plain text when index_all_text is enabled
_TEXT_EXTS = frozenset({".md", ".rst", ".txt", ".sql", ".sh", ".html", ".css"})


def detect_language(path: str) -> str | None:
    lower = path.lower()
    if lower.endswith(".env") or "/.env" in lower or lower.rsplit("/", 1)[-1].startswith(".env"):
        return "config"
    for ext, lang in _EXT_LANGUAGE.items():
        if lower.endswith(ext):
            return lang
    return None


def is_code(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in _CODE_EXTS)


def is_text(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in _TEXT_EXTS)


def module_qname(rel_path: str) -> str:
    """Derive a module name from a repo-relative path.

    Mainframe members (COBOL/JCL/CSD) use the flat library convention — the uppercase member
    stem is the module name, so `COPY MEMBER` / `EXEC PGM=NAME` resolve by name regardless of
    which directory the member sits in.
    """
    p = rel_path.replace("\\", "/").lstrip("/")
    lower = p.lower()
    for ext in _MAINFRAME_EXTS:
        if lower.endswith(ext):
            return p.rsplit("/", 1)[-1][: -len(ext)].upper()
    for ext in (".pyi", ".py", ".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs",
                ".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".env", ".properties"):
        if lower.endswith(ext):
            p = p[: -len(ext)]
            break
    parts = [seg for seg in p.split("/") if seg]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else (rel_path or "root")


__all__ = ["detect_language", "is_code", "is_text", "module_qname"]

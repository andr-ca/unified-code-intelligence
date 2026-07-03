"""Language-server registry: language → how to launch it and where its config lives.

Servers are **detected, not bundled** (docs/lsp-refactoring-recommendations.md §3.2): a user
provides the binary (or a container), and UCI launches it if present. Launch commands and paths
(copybook libraries, dialect flags, venvs) come from ``Config.settings`` via ``UCI_LSP_*`` env keys
— the same optional-settings pattern as the storage backends — so nothing here is hard-coded to one
machine, and a missing server simply means the source is unavailable (never a failed index).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServerSpec:
    """How to start one language server and hand it its language-specific configuration."""

    language: str
    #: default argv; overridable per-repo with ``UCI_LSP_<LANG>_CMD`` (shell-split).
    default_cmd: tuple[str, ...] = ()
    #: LSP ``languageId`` sent on didOpen.
    language_id: str = ""
    #: file suffixes this server should be asked about.
    suffixes: tuple[str, ...] = ()
    #: settings key holding extra search paths (e.g. copybook libs) → sent as initializationOptions.
    paths_setting: str = ""

    def resolved_cmd(self, settings: dict[str, Any]) -> list[str] | None:
        """The argv to launch, honoring a ``UCI_LSP_<LANG>_CMD`` override; ``None`` if unavailable.

        A command is considered available when its first token resolves on ``PATH`` (or is an
        absolute path that exists). Returns ``None`` otherwise so the caller skips the source."""
        override = settings.get(f"lsp_{self.language}_cmd")
        cmd = _split(override) if override else list(self.default_cmd)
        if not cmd:
            return None
        binary = cmd[0]
        if shutil.which(binary) is None and not _is_existing_path(binary):
            return None
        return cmd

    def init_options(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Language-specific ``initializationOptions`` (e.g. copybook search paths for COBOL)."""
        opts: dict[str, Any] = {}
        if self.paths_setting:
            raw = settings.get(self.paths_setting, "")
            paths = [p for p in _split_paths(raw) if p]
            if paths:
                # Che4z reads copybook locations from settings.cobol-lsp.cpy-manager.paths-local;
                # we pass a generic key too so other servers can pick it up.
                opts["cpy-manager"] = {"paths-local": paths}
                opts["copybookPaths"] = paths
        return opts


#: The registry. Add a language by adding a ServerSpec.
REGISTRY: dict[str, ServerSpec] = {
    # Eclipse Che4z COBOL Language Support (Broadcom, Apache-2.0) — headless COBOL LSP.
    # Ships as a Java server; the user points UCI_LSP_COBOL_CMD at the launch script/jar.
    "cobol": ServerSpec(
        language="cobol",
        default_cmd=("cobol-language-support",),  # user-provided wrapper on PATH
        language_id="cobol",
        suffixes=(".cbl", ".cob", ".cpy", ".ccp"),
        paths_setting="lsp_cobol_copybooks",
    ),
    # pyright's language server (npm: pyright) — precise Python cross-references.
    "python": ServerSpec(
        language="python",
        default_cmd=("pyright-langserver", "--stdio"),
        language_id="python",
        suffixes=(".py", ".pyi"),
    ),
    # scip-typescript's language server alternative; here the tsserver-based generic LSP.
    "typescript": ServerSpec(
        language="typescript",
        default_cmd=("typescript-language-server", "--stdio"),
        language_id="typescript",
        suffixes=(".ts", ".tsx", ".js", ".jsx"),
    ),
}


def get_server(language: str) -> ServerSpec | None:
    return REGISTRY.get(language.lower())


def _split(raw: str) -> list[str]:
    import shlex
    return shlex.split(raw)


def _split_paths(raw: str) -> list[str]:
    import os
    return [p.strip() for p in raw.replace(os.pathsep, ",").split(",") if p.strip()]


def _is_existing_path(binary: str) -> bool:
    from pathlib import Path
    p = Path(binary)
    return p.is_absolute() and p.exists()


__all__ = ["ServerSpec", "REGISTRY", "get_server"]

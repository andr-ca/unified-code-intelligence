"""Python structural parser built on the standard-library :mod:`ast` — no native deps required."""

from __future__ import annotations

import ast

from ..core.entities import EntityType
from ..core.ids import qualify
from .base import (
    LanguageParser,
    ParsedCall,
    ParsedImport,
    ParsedReference,
    ParsedSymbol,
    ParseResult,
    resolve_relative_module,
)

_STDLIB_HINT = frozenset(
    {
        "os", "sys", "re", "json", "math", "typing", "collections", "itertools", "functools",
        "dataclasses", "pathlib", "abc", "enum", "datetime", "logging", "asyncio", "subprocess",
        "hashlib", "unittest", "pytest", "numpy", "pandas", "requests", "httpx", "flask", "django",
        "fastapi", "sqlalchemy", "pydantic",
    }
)


def _decorator_name(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - very old pythons
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return ""


class PythonParser(LanguageParser):
    language = "python"
    extensions = (".py", ".pyi")

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            result.errors.append(f"syntax error: {exc}")
            return result

        is_test_module = "test" in path.rsplit("/", 1)[-1].lower()
        visitor = _PyVisitor(module_qname, is_test_module)
        visitor.visit_module(tree)
        result.symbols = visitor.symbols
        result.imports = visitor.imports
        result.calls = visitor.calls
        result.references = visitor.references
        return result


class _PyVisitor:
    def __init__(self, module_qname: str, is_test_module: bool) -> None:
        self.module_qname = module_qname
        self.is_test_module = is_test_module
        self.symbols: list[ParsedSymbol] = []
        self.imports: list[ParsedImport] = []
        self.calls: list[ParsedCall] = []
        self.references: list[ParsedReference] = []

    # -- entry --------------------------------------------------------------
    def visit_module(self, tree: ast.Module) -> None:
        for node in tree.body:
            self._visit_node(node, scope_qname=self.module_qname, class_scope=None)
        # module-level calls (registrations, app = Flask(__name__), ...) belong to the module
        self._collect_calls(tree, caller_qname=self.module_qname)

    # -- dispatch -----------------------------------------------------------
    def _visit_node(self, node: ast.AST, scope_qname: str, class_scope: str | None) -> None:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self._handle_import(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._handle_function(node, scope_qname, class_scope)
        elif isinstance(node, ast.ClassDef):
            self._handle_class(node, scope_qname)
        elif isinstance(node, ast.Assign):
            self._handle_assign(node, scope_qname, class_scope)
        elif isinstance(node, ast.AnnAssign):
            self._handle_ann_assign(node, scope_qname, class_scope)
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For,
                               ast.AsyncFor, ast.While)):
            # compound statements: `if TYPE_CHECKING:` imports, try/except fallback imports,
            # and conditionally defined symbols must not be invisible (feedback.md §4.3)
            self._visit_compound(node, scope_qname, class_scope)

    def _visit_compound(self, node: ast.AST, scope_qname: str, class_scope: str | None) -> None:
        for field_name in ("body", "orelse", "finalbody"):
            for child in getattr(node, field_name, []) or []:
                self._visit_node(child, scope_qname, class_scope)
        for handler in getattr(node, "handlers", []) or []:
            for child in handler.body:
                self._visit_node(child, scope_qname, class_scope)

    # -- imports ------------------------------------------------------------
    def _handle_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                local = alias.asname or top
                self.imports.append(
                    ParsedImport(
                        module=alias.name, names=[], alias=alias.asname,
                        start_line=node.lineno, raw=f"import {alias.name}",
                        external=top in _STDLIB_HINT,
                        binds={local: alias.name},  # alias -> module qname
                    )
                )
        else:
            module = node.module or ""
            resolved = (
                resolve_relative_module(self.module_qname, node.level, module)
                if node.level
                else module
            )
            top = resolved.split(".")[0]
            binds: dict[str, str] = {}
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                # local name resolves to the imported symbol's qualified name
                binds[local] = f"{resolved}.{alias.name}" if resolved else alias.name
            self.imports.append(
                ParsedImport(
                    module=resolved,
                    names=[a.name for a in node.names],
                    alias=None,
                    start_line=node.lineno,
                    raw=f"from {'.' * node.level}{module} import ...",
                    external=(node.level == 0 and top in _STDLIB_HINT),
                    binds=binds,
                )
            )

    # -- functions ----------------------------------------------------------
    def _handle_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, scope_qname: str, class_scope: str | None
    ) -> None:
        qname = qualify(scope_qname, node.name)
        is_method = class_scope is not None
        kind = EntityType.METHOD if is_method else EntityType.FUNCTION
        if (self.is_test_module and node.name.startswith("test")) or node.name.startswith("test_"):
            kind = EntityType.TEST
        decorators = [_decorator_name(d) for d in node.decorator_list]
        args = [a.arg for a in node.args.args]
        symbol = ParsedSymbol(
            name=node.name,
            qualified_name=qname,
            kind=kind,
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            parent_qname=scope_qname,
            signature=f"{node.name}({', '.join(args)})",
            docstring=ast.get_docstring(node) or "",
            decorators=decorators,
            is_exported=not node.name.startswith("_"),
            attributes={"async": isinstance(node, ast.AsyncFunctionDef), "params": args},
        )
        self.symbols.append(symbol)
        # descend into the body: collect calls (caller = this function) + nested defs
        self._collect_calls(node, caller_qname=qname)
        for child in node.body:
            self._visit_node(child, scope_qname=qname, class_scope=None)

    # -- classes ------------------------------------------------------------
    def _handle_class(self, node: ast.ClassDef, scope_qname: str) -> None:
        qname = qualify(scope_qname, node.name)
        bases = [_base_name(b) for b in node.bases if _base_name(b)]
        symbol = ParsedSymbol(
            name=node.name,
            qualified_name=qname,
            kind=EntityType.CLASS,
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            parent_qname=scope_qname,
            docstring=ast.get_docstring(node) or "",
            bases=bases,
            decorators=[_decorator_name(d) for d in node.decorator_list],
            is_exported=not node.name.startswith("_"),
        )
        self.symbols.append(symbol)
        for base in bases:
            self.references.append(
                ParsedReference(name=base.split(".")[-1], from_qname=qname,
                                 start_line=node.lineno, kind="base")
            )
        for child in node.body:
            self._visit_node(child, scope_qname=qname, class_scope=qname)

    # -- module/class-level variables --------------------------------------
    def _handle_assign(self, node: ast.Assign, scope_qname: str, class_scope: str | None) -> None:
        # only capture simple NAME = ... at module or class level (config keys, constants)
        if scope_qname != self.module_qname and class_scope is None:
            return
        for target in node.targets:
            if isinstance(target, ast.Name):
                qname = qualify(scope_qname, target.id)
                self.symbols.append(
                    ParsedSymbol(
                        name=target.id, qualified_name=qname, kind=EntityType.VARIABLE,
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                        parent_qname=scope_qname,
                        is_exported=not target.id.startswith("_"),
                        attributes={"constant": target.id.isupper()},
                    )
                )

    def _handle_ann_assign(self, node: ast.AnnAssign, scope_qname: str, class_scope: str | None) -> None:
        """``MAX_RETRIES: int = 3`` — the dominant style for typed module/class constants."""
        if scope_qname != self.module_qname and class_scope is None:
            return
        if not isinstance(node.target, ast.Name):
            return
        name = node.target.id
        try:
            annotation = ast.unparse(node.annotation)
        except Exception:  # pragma: no cover
            annotation = ""
        self.symbols.append(
            ParsedSymbol(
                name=name, qualified_name=qualify(scope_qname, name), kind=EntityType.VARIABLE,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                parent_qname=scope_qname,
                is_exported=not name.startswith("_"),
                attributes={"constant": name.isupper(), "annotation": annotation},
            )
        )

    # -- calls --------------------------------------------------------------
    def _iter_calls_shallow(self, node: ast.AST):
        """Yield ``ast.Call`` nodes inside *node* without descending into nested scopes.

        Nested function/class bodies are visited separately so their calls are attributed to the
        correct enclosing symbol (not double-counted here).
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call):
                yield child
            yield from self._iter_calls_shallow(child)

    def _collect_calls(self, func_node: ast.AST, caller_qname: str) -> None:
        local_types = self._infer_local_types(func_node)
        for node in self._iter_calls_shallow(func_node):
            func = node.func
            if isinstance(func, ast.Name):
                self.calls.append(
                    ParsedCall(callee_name=func.id, caller_qname=caller_qname,
                               start_line=node.lineno, receiver=None)
                )
                if func.id and func.id[0].isupper():
                    self.references.append(
                        ParsedReference(name=func.id, from_qname=caller_qname,
                                        start_line=node.lineno, kind="instantiation")
                    )
            elif isinstance(func, ast.Attribute):
                receiver = None
                if isinstance(func.value, ast.Name):
                    receiver = func.value.id
                self.calls.append(
                    ParsedCall(callee_name=func.attr, caller_qname=caller_qname,
                               start_line=node.lineno, receiver=receiver,
                               receiver_type=local_types.get(receiver) if receiver else None)
                )

    def _infer_local_types(self, func_node: ast.AST) -> dict[str, str]:
        """Lightweight local type inference: ``x = ClassName(...)``, ``x: ClassName``, and annotated
        parameters map a local variable to a class name (feeds receiver-aware call resolution, R2)."""
        types: dict[str, str] = {}
        if isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in list(func_node.args.args) + list(func_node.args.kwonlyargs):
                if isinstance(arg.annotation, ast.Name):
                    types[arg.arg] = arg.annotation.id
        for node in ast.walk(func_node):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                callee = node.value.func
                if isinstance(callee, ast.Name) and callee.id[:1].isupper():
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            types[target.id] = callee.id
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and isinstance(node.annotation, ast.Name):
                types[node.target.id] = node.annotation.id
        return types


__all__ = ["PythonParser"]

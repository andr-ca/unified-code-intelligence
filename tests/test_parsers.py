"""Parser tests: Python (ast), JavaScript/TypeScript (scanner), and config-key extraction."""

from __future__ import annotations

from uci.core.entities import EntityType
from uci.parser.config_parser import ConfigParser
from uci.parser.javascript_parser import JavaScriptParser
from uci.parser.python_parser import PythonParser

PY = '''\
from pkg.mod import Helper
from . import sibling

class Base:
    pass

class Widget(Base):
    """A widget."""
    def render(self, x):
        return Helper().build(x)

def top():
    return Widget().render(1)
'''

JS = '''\
import { thing } from "./mod";

export class App extends Base {
  constructor(x) { this.x = x; }
  start() {
    return helper(this.x);
  }
}

export function helper(x) {
  return x + 1;
}
'''


def _by_name(result, name):
    return next((s for s in result.symbols if s.name == name), None)


def test_python_symbols_and_kinds():
    result = PythonParser().parse(PY, "pkg/widget.py", "pkg.widget")
    kinds = {s.name: s.kind for s in result.symbols}
    assert kinds["Base"] is EntityType.CLASS
    assert kinds["Widget"] is EntityType.CLASS
    assert kinds["render"] is EntityType.METHOD
    assert kinds["top"] is EntityType.FUNCTION
    widget = _by_name(result, "Widget")
    assert "Base" in widget.bases
    assert widget.qualified_name == "pkg.widget.Widget"


def test_python_imports_absolute_and_relative():
    result = PythonParser().parse(PY, "pkg/widget.py", "pkg.widget")
    modules = {imp.module for imp in result.imports}
    assert "pkg.mod" in modules
    assert "pkg" in modules  # relative "from . import sibling" resolves to package


def test_python_calls_attributed_to_caller():
    result = PythonParser().parse(PY, "pkg/widget.py", "pkg.widget")
    top_calls = {c.callee_name for c in result.calls if c.caller_qname == "pkg.widget.top"}
    assert "render" in top_calls
    render_calls = {c.callee_name for c in result.calls if c.caller_qname.endswith("render")}
    assert "build" in render_calls


def test_python_test_detection():
    src = "def test_thing():\n    assert True\n"
    result = PythonParser().parse(src, "tests/test_x.py", "tests.test_x")
    assert _by_name(result, "test_thing").kind is EntityType.TEST


def test_python_malformed_never_raises():
    result = PythonParser().parse("def broken(:\n", "b.py", "b")
    assert result.errors  # recorded, not raised


def test_javascript_symbols_and_inheritance():
    result = JavaScriptParser().parse(JS, "web/app.ts", "web.app")
    names = {s.name: s.kind for s in result.symbols}
    assert names["App"] is EntityType.CLASS
    assert names["start"] is EntityType.METHOD
    assert names["helper"] is EntityType.FUNCTION
    app = _by_name(result, "App")
    assert "Base" in app.bases


def test_javascript_call_attribution():
    result = JavaScriptParser().parse(JS, "web/app.ts", "web.app")
    start_calls = {c.callee_name for c in result.calls if c.caller_qname.endswith("start")}
    assert "helper" in start_calls


def test_javascript_ignores_braces_in_strings():
    src = 'const s = "class Fake {";\nexport function real() { return 1; }\n'
    result = JavaScriptParser().parse(src, "a.js", "a")
    names = {s.name for s in result.symbols}
    assert "real" in names and "Fake" not in names


def test_config_key_extraction_env():
    src = "FEATURE_X=1\nMAX_DISCOUNT=50\n# comment\n"
    result = ConfigParser().parse(src, "config.env", "config")
    keys = {s.name for s in result.symbols}
    assert keys == {"FEATURE_X", "MAX_DISCOUNT"}
    assert all(s.kind is EntityType.CONFIG_KEY for s in result.symbols)


def test_config_key_extraction_json():
    result = ConfigParser().parse('{"host": "x", "port": 5}', "settings.json", "settings")
    assert {s.name for s in result.symbols} == {"host", "port"}

"""Unit tests for the llm_eval CLI helpers (model resolution + scope filtering).

These are pure functions — no LLM calls — so they run in CI. They guard the silent-failure mode
where a bad alias makes you *think* you benchmarked the frontier while actually running a local model.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import llm_eval as L  # noqa: E402


def _pm(specs):
    return [(s.protocol, s.model) for s in specs]


def test_resolve_group_expands_to_members():
    assert _pm(L.resolve_models("frontier")) == [
        ("freellm", "qwen3-coder-480b"), ("freellm", "gpt-4.1")]
    assert _pm(L.resolve_models("local")) == [("ollama", "qwen3.5:4b"), ("ollama", "gemma4:e4b")]


def test_resolve_mixes_tiers_and_forms():
    # alias + raw protocol:model + bare name (on default protocol), all in one string
    got = _pm(L.resolve_models("qwen-coder,freellm:gpt-4.1,qwen3.5:2b", default_protocol="ollama"))
    assert got == [("freellm", "qwen3-coder-480b"), ("freellm", "gpt-4.1"), ("ollama", "qwen3.5:2b")]


def test_resolve_dedupes_by_protocol_model():
    # `local` includes qwen4b; naming it again must not double it
    assert _pm(L.resolve_models("local,qwen4b")) == [
        ("ollama", "qwen3.5:4b"), ("ollama", "gemma4:e4b")]


def test_bare_name_uses_default_protocol():
    assert _pm(L.resolve_models("some-model", default_protocol="freellm")) == [
        ("freellm", "some-model")]


def test_ollama_model_with_colon_is_not_mistaken_for_protocol():
    # 'qwen3.5:4b' has a colon but 'qwen3.5' is not a protocol → treated as a bare model name
    assert _pm(L.resolve_models("qwen3.5:4b", default_protocol="ollama")) == [
        ("ollama", "qwen3.5:4b")]


def test_scope_smoke_is_a_strict_subset_without_tools():
    smoke = {t[1] for t in L.select_tasks("smoke", tools=False)}
    full = {t[1] for t in L.select_tasks("full", tools=False)}
    assert smoke and smoke < full
    assert "candidates_restraint_when_opaque" in smoke  # the safety task is always in smoke


def test_tools_adds_agentic_tasks_only_with_an_engine():
    assert not any("agentic" in t[1] for t in L.select_tasks("full", tools=True, agentic_engine=None))
    with_engine = [t[1] for t in L.select_tasks("full", tools=True, agentic_engine=object())]
    assert "agentic_cross_file_resolution" in with_engine and "agentic_restraint" in with_engine


def test_interactive_flow_frontier_smoke_with_tools():
    """Simulate user selecting: frontier group, smoke scope, yes to tools."""
    simulated_input = "frontier\nsmoke\ny\ny\n"
    with patch("sys.stdin", StringIO(simulated_input)):
        with patch("sys.stdout", new_callable=StringIO):
            models_str, scope, tools = L._interactive()
    assert models_str == "frontier"
    assert scope == "smoke"
    assert tools is True
    # Verify models resolve correctly
    specs = L.resolve_models(models_str)
    assert len(specs) == 2 and all(s.protocol == "freellm" for s in specs)


def test_interactive_flow_single_model_full_no_tools():
    """Simulate user selecting: single model, full scope, no tools."""
    simulated_input = "qwen-coder\nfull\nn\ny\n"
    with patch("sys.stdin", StringIO(simulated_input)):
        with patch("sys.stdout", new_callable=StringIO):
            models_str, scope, tools = L._interactive()
    assert models_str == "qwen-coder"
    assert scope == "full"
    assert tools is False


def test_interactive_retries_on_bad_model():
    """User enters bad model, loop prompts again, corrects it."""
    # input 1: invalid-alias (rejected) -> loops back to Step 1
    # input 2: qwen4b (accepted) -> continues to Step 2
    # input 3: smoke (accepted) -> continues to Step 3
    # input 4: y (accepted) -> continues to Step 4 (review)
    # input 5: y (accepted) -> returns
    simulated_input = "invalid-alias\nqwen4b\nsmoke\ny\ny\n"
    with patch("sys.stdin", StringIO(simulated_input)):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            models_str, scope, tools = L._interactive()
    assert models_str == "qwen4b"
    output = mock_stdout.getvalue()
    assert "not found" in output  # Error message on first attempt


def test_interactive_retries_on_bad_scope():
    """User enters bad scope, loop prompts again, corrects it."""
    # input 1: qwen4b (accepted) -> continues to Step 2
    # input 2: invalid-scope (rejected) -> loops back to Step 2
    # input 3: smoke (accepted) -> continues to Step 3
    # input 4: n (accepted) -> continues to Step 4 (review)
    # input 5: y (accepted) -> returns
    simulated_input = "qwen4b\ninvalid-scope\nsmoke\nn\ny\n"
    with patch("sys.stdin", StringIO(simulated_input)):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            models_str, scope, tools = L._interactive()
    assert scope == "smoke"
    output = mock_stdout.getvalue()
    assert "Unknown scope" in output  # Error message on first attempt

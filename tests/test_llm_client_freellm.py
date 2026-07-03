"""freellm LLM protocol — OpenAI-compatible gateway on localhost:3001 (auto-select model)."""

from __future__ import annotations

import json
import urllib.request

import pytest

from uci import Config
from uci.enrich.llm_client import LlmClient, LlmError


class _FakeResp:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def capture(monkeypatch):
    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["payload"] = json.loads(req.data.decode())
        return _FakeResp({"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_freellm_defaults_to_local_gateway_and_openai_wire(capture):
    client = LlmClient(Config(llm_protocol="freellm"))
    assert client.base_url == "http://localhost:3001/v1"  # protocol default URL
    assert client.model == ""                              # empty -> gateway auto-selects
    assert client.complete("s", "u") == "hi"
    assert capture["url"] == "http://localhost:3001/v1/chat/completions"
    # empty model must be omitted so the gateway can pick the best model itself
    assert "model" not in capture["payload"]
    assert capture["payload"]["temperature"] == 0


def test_freellm_includes_model_and_key_when_set(capture):
    cfg = Config(llm_protocol="freellm", llm_model="best-model",
                 settings={"llm_api_key": "sk-free"})
    assert LlmClient(cfg).complete("s", "u") == "hi"
    assert capture["payload"]["model"] == "best-model"
    assert capture["headers"].get("Authorization") == "Bearer sk-free"


def test_freellm_respects_custom_url(capture):
    cfg = Config(llm_protocol="freellm", llm_url="http://localhost:9000/v1")
    LlmClient(cfg).complete("s", "u")
    assert capture["url"] == "http://localhost:9000/v1/chat/completions"


def test_freellm_available_requires_api_key():
    # freellm needs a key (endpoint rejects unauthenticated requests), so it's only
    # "available" once a key is configured — no false advertising when unset.
    assert LlmClient(Config(llm_protocol="freellm")).available is False
    assert LlmClient(Config(llm_protocol="freellm",
                            settings={"llm_api_key": "sk-free"})).available is True


def test_unknown_protocol_still_rejected():
    with pytest.raises(LlmError):
        LlmClient(Config(llm_protocol="nope"))

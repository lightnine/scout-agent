"""Prompt cache 与 tools cache block 测试。"""

from __future__ import annotations

from types import SimpleNamespace

from scout.llm.cache import read_cached_tokens, stable_tool_schemas, tools_cache_fingerprint


def test_stable_tool_schemas_sorts_by_name():
    schemas = [
        {"type": "function", "function": {"name": "z_tool", "description": "z", "parameters": {}}},
        {"type": "function", "function": {"name": "a_tool", "description": "a", "parameters": {}}},
    ]
    ordered = stable_tool_schemas(schemas)
    assert [s["function"]["name"] for s in ordered] == ["a_tool", "z_tool"]


def test_tools_cache_fingerprint_is_stable():
    schemas = [
        {"type": "function", "function": {"name": "b", "description": "b", "parameters": {}}},
        {"type": "function", "function": {"name": "a", "description": "a", "parameters": {}}},
    ]
    assert tools_cache_fingerprint(schemas) == tools_cache_fingerprint(list(reversed(schemas)))


def test_read_cached_tokens_from_object():
    usage = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=800))
    assert read_cached_tokens(usage) == 800


def test_read_cached_tokens_from_dict():
    usage = SimpleNamespace(prompt_tokens_details={"cached_tokens": 512})
    assert read_cached_tokens(usage) == 512


def test_read_cached_tokens_missing():
    assert read_cached_tokens(None) == 0
    assert read_cached_tokens(SimpleNamespace()) == 0

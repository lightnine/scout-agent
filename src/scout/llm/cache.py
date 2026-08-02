"""Prompt prefix cache 辅助。

智谱 GLM（open.bigmodel.cn）等 OpenAI 兼容端点通常提供**隐式**上下文缓存：
无需 cache_control，只要请求前缀（messages + tools）字节级一致即可命中。
响应里读 ``usage.prompt_tokens_details.cached_tokens`` 验证。

工具 schema 体积大且整轮 run 内不变，单独做稳定序列化（sorted + memoized），
避免 dict 顺序抖动导致 cache miss。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_tool_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按工具名排序，保证同一注册表每次 API 调用的 tools 数组一致。"""
    return sorted(schemas, key=lambda item: item["function"]["name"])


def tools_cache_fingerprint(schemas: list[dict[str, Any]]) -> str:
    """用于 trace 的短指纹，方便确认 tools cache block 是否变化。"""
    payload = json.dumps(stable_tool_schemas(schemas), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def read_cached_tokens(usage: Any) -> int:
    """从 OpenAI 兼容 usage 对象读取 prefix cache 命中数。"""
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    if isinstance(details, dict):
        return int(details.get("cached_tokens") or 0)
    return int(getattr(details, "cached_tokens", 0) or 0)

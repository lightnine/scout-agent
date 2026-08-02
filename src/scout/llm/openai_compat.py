"""OpenAI 兼容协议客户端。

覆盖智谱 GLM、DeepSeek、Kimi、通义、OpenAI 以及 vLLM/Ollama 等本地推理服务，
因为它们都实现了 ``/chat/completions`` 兼容端点。
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from .base import DeltaHandler, LLMResponse, Message, ToolCall, Usage
from .cache import read_cached_tokens

RETRIABLE = (RateLimitError, APIConnectionError, APITimeoutError)
MAX_RETRIES = 3


class OpenAICompatClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        embedding_model: str = "",
        temperature: float = 0.3,
        timeout: float = 180.0,
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.embedding_model = embedding_model
        self.temperature = temperature
        self.total_usage = Usage()
        self._lock = threading.Lock()  # 子 Agent 并行时统计不能串

    # ---------------------------------------------------------------- chat
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
        on_delta: DeltaHandler | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        started = time.monotonic()
        response = _with_retry(lambda: self._dispatch(payload, stream, on_delta))
        response.latency_ms = int((time.monotonic() - started) * 1000)
        response.usage.calls = 1
        with self._lock:
            self.total_usage = self.total_usage + response.usage
        return response

    def _dispatch(
        self, payload: dict[str, Any], stream: bool, on_delta: DeltaHandler | None
    ) -> LLMResponse:
        return self._chat_stream(payload, on_delta) if stream else self._chat_once(payload)

    def _chat_once(self, payload: dict[str, Any]) -> LLMResponse:
        completion = self._client.chat.completions.create(**payload)
        choice = completion.choices[0]
        msg = choice.message
        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=_safe_json(tc.function.arguments),
                raw_arguments=tc.function.arguments or "",
            )
            for tc in (msg.tool_calls or [])
        ]
        usage = Usage(
            prompt_tokens=getattr(completion.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(completion.usage, "completion_tokens", 0) or 0,
            cached_tokens=read_cached_tokens(completion.usage),
        )
        return LLMResponse(
            message=Message(role="assistant", content=msg.content or "", tool_calls=tool_calls),
            usage=usage,
            finish_reason=choice.finish_reason or "stop",
        )

    def _chat_stream(self, payload: dict[str, Any], on_delta: DeltaHandler | None) -> LLMResponse:
        """流式模式。

        tool_call 在流里是按 index 分片下发的：函数名通常只在第一片出现，
        arguments 逐段拼接。所以必须按 index 累积完再解析 JSON。
        """
        stream = self._client.chat.completions.create(**payload, stream=True)
        content_parts: list[str] = []
        partial: dict[int, dict[str, str]] = {}
        finish_reason = "stop"
        usage = Usage()

        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = Usage(
                    prompt_tokens=chunk.usage.prompt_tokens or 0,
                    completion_tokens=chunk.usage.completion_tokens or 0,
                    cached_tokens=read_cached_tokens(chunk.usage),
                )
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                if on_delta:
                    on_delta(delta.content)
            for tc in delta.tool_calls or []:
                slot = partial.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

        tool_calls = [
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=_safe_json(slot["arguments"]),
                raw_arguments=slot["arguments"],
            )
            for index, slot in sorted(partial.items())
            if slot["name"]
        ]
        content = "".join(content_parts)
        if usage.total == 0:  # 部分服务端流式不返回 usage，退化为估算
            usage = Usage(completion_tokens=len(content) // 4)
        return LLMResponse(
            message=Message(role="assistant", content=content, tool_calls=tool_calls),
            usage=usage,
            finish_reason=finish_reason,
        )

    # ----------------------------------------------------------- embedding
    def embed(self, texts: list[str]) -> list[list[float]]:
        """把文本批量转成向量。未配置 embedding 模型时返回空列表，
        调用方据此退化为关键词检索。"""
        if not self.embedding_model or not texts:
            return []
        try:
            result = _with_retry(
                lambda: self._client.embeddings.create(model=self.embedding_model, input=texts)
            )
        except Exception:
            return []
        return [item.embedding for item in result.data]


def _with_retry(fn: Callable[[], Any]) -> Any:
    """指数退避重试。只重试限流/网络/5xx，4xx 直接抛出（重试也没用）。"""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except RETRIABLE as exc:
            last_error = exc
        except APIStatusError as exc:
            if exc.status_code < 500:
                raise
            last_error = exc
        time.sleep(min(2**attempt + random.random(), 8))
    raise RuntimeError(f"调用模型失败（已重试 {MAX_RETRIES} 次）：{last_error}") from last_error


def _safe_json(raw: str | None) -> dict[str, Any]:
    """模型偶尔会吐出非法 JSON。这里兜底成一个可读结构，
    让错误以"工具参数错误"的形式回到模型手里，而不是让整个 loop 崩掉。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__invalid_json__": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}

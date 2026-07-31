"""工具系统的地基。

核心理念：**写一个工具 = 写一个带类型注解的普通 Python 函数**。
函数签名会被自动翻译成 function-calling 需要的 JSON Schema，
避免"schema 和实现两处维护、改一处忘一处"这个经典坑。
"""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

CTX_PARAM = "ctx"


class Risk(IntEnum):
    """风险等级，决定是否需要人工确认。"""

    SAFE = 0  # 只读、无副作用（搜索、读文件、查证据）
    CAUTION = 1  # 有副作用但可控（写文件、发 HTTP 请求）
    DANGEROUS = 2  # 可能不可逆（执行命令、删除数据）


@dataclass(slots=True)
class ToolResult:
    """工具的统一返回值。

    ``content`` 回灌给模型；``display`` 给终端展示（更短）。
    工具失败时**不抛异常**而是返回 ok=False：让模型看见错误文本并自行纠正，
    这是 Agent 具备自愈能力的关键——异常会中断 loop，错误文本只是一次观察。
    """

    ok: bool
    content: str
    display: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, content: str, display: str = "", **meta: Any) -> ToolResult:
        return cls(True, content, display or _first_line(content), meta)

    @classmethod
    def failure(cls, message: str, **meta: Any) -> ToolResult:
        return cls(False, f"[工具执行失败] {message}", f"失败：{_first_line(message)}", meta)


@dataclass
class ToolContext:
    """注入给工具的运行时上下文。

    工具函数只要把第一个参数命名为 ``ctx``，就能拿到它，
    并且这个参数不会出现在暴露给模型的 schema 里。
    """

    workspace: Path
    settings: Any = None
    llm: Any = None
    memory: Any = None  # SemanticMemory：跨会话的长期记忆
    evidence: Any = None  # EvidenceStore：本次调研抓到的原始资料
    session: Any = None  # 当前会话（计划、来源编号都挂在上面）
    spawn: Any = None  # Callable：派生子 Agent，由 Agent 注入
    emit: Any = None  # Callable：向事件总线发事件

    def resolve(self, path: str) -> Path:
        """把工具传入的路径解析成绝对路径，并禁止逃出工作区。

        模型有时会"顺手"写到 ~/ 或 /tmp，这里做硬性拦截。
        """
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = self.workspace / target
        target = target.resolve()
        workspace = self.workspace.resolve()
        if target != workspace and workspace not in target.parents:
            raise PermissionError(f"路径越界：{target} 不在工作区 {workspace} 内")
        return target


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    risk: Risk = Risk.SAFE
    concurrency_safe: bool = True
    needs_ctx: bool = False

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, ctx: ToolContext | None = None, /, **kwargs: Any) -> ToolResult:
        try:
            self._validate(kwargs)
            result = self.fn(ctx, **kwargs) if self.needs_ctx else self.fn(**kwargs)
        except Exception as exc:
            return ToolResult.failure(f"{type(exc).__name__}: {exc}")
        if isinstance(result, ToolResult):
            return result
        return ToolResult.success(str(result))

    def _validate(self, kwargs: dict[str, Any]) -> None:
        properties = self.parameters.get("properties", {})
        missing = set(self.parameters.get("required", [])) - kwargs.keys()
        if missing:
            raise ValueError(f"缺少必填参数：{', '.join(sorted(missing))}")
        unknown = kwargs.keys() - properties.keys()
        if unknown:
            raise ValueError(
                f"不存在的参数：{', '.join(sorted(unknown))}；"
                f"可用参数：{', '.join(sorted(properties)) or '无'}"
            )


def tool(
    *,
    name: str | None = None,
    risk: Risk = Risk.SAFE,
    concurrency_safe: bool | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """把普通函数装饰成 Tool。

    示例::

        @tool(risk=Risk.CAUTION)
        def save_note(
            ctx,
            title: Annotated[str, "笔记标题"],
            content: Annotated[str, "正文"],
            tags: Annotated[list[str], "标签"] = [],
        ) -> str:
            '''保存一条调研笔记。'''

    函数的 docstring 会成为工具描述（模型选工具主要看它，值得好好写），
    ``Annotated[T, "说明"]`` 里的说明会成为参数描述。
    """

    def decorator(fn: Callable[..., Any]) -> Tool:
        signature = inspect.signature(fn)
        params = list(signature.parameters.values())
        needs_ctx = bool(params) and params[0].name == CTX_PARAM
        if needs_ctx:
            params = params[1:]

        hints = typing.get_type_hints(fn, include_extras=True)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in params:
            schema, description = _schema_from_annotation(hints.get(param.name, str))
            if description:
                schema["description"] = description
            if param.default is inspect.Parameter.empty:
                required.append(param.name)
            else:
                schema["default"] = param.default
            properties[param.name] = schema

        return Tool(
            name=name or fn.__name__,
            description=(inspect.getdoc(fn) or "").strip(),
            parameters={"type": "object", "properties": properties, "required": required},
            fn=fn,
            risk=risk,
            concurrency_safe=(risk == Risk.SAFE) if concurrency_safe is None else concurrency_safe,
            needs_ctx=needs_ctx,
        )

    return decorator


_PRIMITIVES: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema_from_annotation(annotation: Any) -> tuple[dict[str, Any], str]:
    """把 Python 类型注解翻译成 JSON Schema 片段，返回 (schema, description)。"""
    origin = get_origin(annotation)

    if origin is Annotated:
        args = get_args(annotation)
        schema, _ = _schema_from_annotation(args[0])
        description = " ".join(str(a) for a in args[1:] if isinstance(a, str))
        return schema, description

    if origin is Literal:
        options = list(get_args(annotation))
        kind = _PRIMITIVES.get(type(options[0]), "string") if options else "string"
        return {"type": kind, "enum": options}, ""

    if origin in (typing.Union, types.UnionType):  # Optional[X] / X | None
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _schema_from_annotation(non_none[0])
        return {"type": "string"}, ""

    if origin in (list, set, tuple):
        args = get_args(annotation)
        item_schema = _schema_from_annotation(args[0])[0] if args else {"type": "string"}
        return {"type": "array", "items": item_schema}, ""

    if origin is dict or annotation is dict:
        return {"type": "object"}, ""

    if annotation in _PRIMITIVES:
        return {"type": _PRIMITIVES[annotation]}, ""

    return {"type": "string"}, ""


def _first_line(text: str, limit: int = 100) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    line = stripped.splitlines()[0]
    return line if len(line) <= limit else line[:limit] + "…"

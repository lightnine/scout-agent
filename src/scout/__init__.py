"""Scout：一个从零实现的调研型 AI Agent。

分层结构（自下而上）::

    scout.llm            模型抽象层：把各家 API 统一成 Message / ToolCall
    scout.tools          工具层：函数签名 -> JSON Schema，注册表负责鉴权与执行
    scout.memory         记忆层：工作记忆(带压缩) / 情景记忆(SQLite) / 语义记忆 / 证据库
    scout.core           编排层：Agent Loop、提示词组装、子 Agent、事件流
    scout.observability  可观测层：结构化 Trace 与成本统计
    scout.cli            交互层：终端 REPL
"""

__version__ = "0.1.0"

from .config import Settings, load_settings

__all__ = ["Settings", "load_settings", "__version__"]

"""运行时装配。

把配置、模型客户端、存储、记忆、工具、Agent 这些零件按依赖顺序拼起来。
所有"谁依赖谁"的知识都集中在这一个文件里，其余模块只依赖被注入的对象——
这样单测里换成 FakeLLM、内存库都不用改业务代码。
"""

from __future__ import annotations

from typing import Any

from .approval import ApprovalGateway
from .config import Settings
from .core.agent import Agent
from .core.events import EventBus
from .core.session import Session
from .llm.openai_compat import OpenAICompatClient
from .memory.semantic import SemanticMemory
from .memory.store import Store
from .observability.trace import TraceRecorder
from .permissions import PolicyApprover
from .tools import build_registry
from .tools.base import ToolContext


class Runtime:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus | None = None,
        approver: Any = None,
        llm: Any = None,
        enable_trace: bool = True,
        approval_gateway: ApprovalGateway | None = None,
    ) -> None:
        self.settings = settings
        self.bus = bus or EventBus()
        self.store = Store(settings.db_path)
        self.llm = llm or OpenAICompatClient(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
            embedding_model=settings.embedding_model,
            temperature=settings.temperature,
            timeout=settings.request_timeout,
        )
        self.memory = SemanticMemory(self.store, self.llm)
        self.approver = approver or PolicyApprover(settings.permission_mode)
        self.approval_gateway = approval_gateway or getattr(self.approver, "gateway", None)
        self.trace: TraceRecorder | None = None
        if enable_trace:
            self.trace = TraceRecorder(settings.trace_path)
            self.bus.subscribe(self.trace.handle)

    # ---------------------------------------------------------------- 会话
    def new_session(self, title: str = "") -> Session:
        return Session.create(self.store, self.llm, self.settings.compact_threshold, title)

    def resume_session(self, session_id: str) -> Session:
        return Session.resume(self.store, self.llm, session_id, self.settings.compact_threshold)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.list_sessions(limit)

    # ---------------------------------------------------------------- Agent
    def build_agent(self, session: Session) -> Agent:
        ctx = ToolContext(
            workspace=self.settings.workspace,
            settings=self.settings,
            llm=self.llm,
            memory=self.memory,
            evidence=session.evidence,
            session=session,
            emit=lambda name, data: self.bus.emit(name, data),
        )
        registry = build_registry(ctx, self.approver, self.settings.parallel_tool_calls)
        agent = Agent(
            llm=self.llm,
            settings=self.settings,
            session=session,
            registry=registry,
            bus=self.bus,
            memory=self.memory,
            approval_gateway=self.approval_gateway,
        )
        # 子 Agent 的派生函数依赖 Agent 自身，因此在 Agent 创建后回填
        ctx.spawn = agent.make_spawner()
        return agent

    def close(self) -> None:
        self.store.close()

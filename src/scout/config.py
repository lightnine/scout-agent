"""集中式配置：所有可调项都来自环境变量，方便本地 .env 与容器部署共用一套代码。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _env(*names: str, default: str = "") -> str:
    """按优先级读取多个环境变量名，返回第一个非空值。

    支持别名是为了能直接复用已有的 .env（比如别的项目里写的 ZAI_API_KEY）。
    """
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    # --- 模型 ---
    api_key: str = ""
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    model: str = "glm-5.2"
    fast_model: str = ""
    embedding_model: str = ""
    temperature: float = 0.3
    request_timeout: float = 180.0

    # --- 搜索 ---
    search_provider: str = "duckduckgo"
    tavily_api_key: str = ""
    serper_api_key: str = ""

    # --- Agent 行为 ---
    max_steps: int = 30
    subagent_max_steps: int = 12
    max_subagents: int = 3
    compact_threshold: int = 16000
    permission_mode: str = "ask"
    parallel_tool_calls: int = 4

    # --- 路径 ---
    workspace: Path = field(default_factory=Path.cwd)
    home: Path = field(default_factory=lambda: Path.cwd() / ".scout")

    @property
    def db_path(self) -> Path:
        return self.home / "scout.db"

    @property
    def trace_path(self) -> Path:
        return self.home / "traces.jsonl"

    @property
    def reports_dir(self) -> Path:
        return self.workspace / "reports"

    def worker_model(self) -> str:
        """子 Agent 使用的模型，未单独配置时回落到主模型。"""
        return self.fast_model or self.model

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "缺少 LLM API Key。请把 .env.example 复制为 .env 并填写 LLM_API_KEY。\n"
                "智谱开放平台可免费领取：https://bigmodel.cn/usercenter/proj-mgmt/apikeys"
            )


def load_settings(workspace: Path | None = None) -> Settings:
    load_dotenv(override=False)
    ws = (workspace or Path.cwd()).resolve()

    home = Path(_env("AGENT_HOME", default=".scout"))
    if not home.is_absolute():
        home = ws / home

    settings = Settings(
        api_key=_env("LLM_API_KEY", "ZAI_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"),
        base_url=_env(
            "LLM_BASE_URL", "OPENAI_BASE_URL", default="https://open.bigmodel.cn/api/paas/v4/"
        ),
        model=_env("LLM_MODEL", "ZHIPU_MODEL", default="glm-5.2"),
        fast_model=_env("LLM_FAST_MODEL"),
        embedding_model=_env("LLM_EMBEDDING_MODEL", "ZHIPU_EMBEDDING_MODEL"),
        search_provider=_env("SEARCH_PROVIDER", default="duckduckgo").lower(),
        tavily_api_key=_env("TAVILY_API_KEY"),
        serper_api_key=_env("SERPER_API_KEY"),
        max_steps=_env_int("AGENT_MAX_STEPS", 30),
        subagent_max_steps=_env_int("AGENT_SUBAGENT_MAX_STEPS", 12),
        max_subagents=_env_int("AGENT_MAX_SUBAGENTS", 3),
        compact_threshold=_env_int("AGENT_COMPACT_THRESHOLD", 16000),
        permission_mode=_env("AGENT_PERMISSION_MODE", default="ask").lower(),
        workspace=ws,
        home=home,
    )
    settings.ensure_dirs()
    return settings

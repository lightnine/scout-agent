"""把 Scout 当库用：不走 CLI，直接在自己的程序里跑 Agent。

    uv run python examples/programmatic.py
"""

from __future__ import annotations

from pathlib import Path

from scout.config import load_settings
from scout.core.events import EventBus, EventType
from scout.runtime import Runtime


def main() -> None:
    settings = load_settings(Path.cwd())
    settings.validate()
    settings.permission_mode = "auto"  # 无人值守
    settings.max_steps = 8

    # 订阅事件流：想接 Web / 飞书机器人，换掉这个订阅者就行
    bus = EventBus()
    bus.subscribe(
        lambda e: print(f"[{e.agent}] {e.type.value}: {list(e.data)[:3]}")
        if e.type is not EventType.LLM_DELTA
        else None
    )

    runtime = Runtime(settings, bus=bus)
    try:
        session = runtime.new_session("程序化调用示例")
        agent = runtime.build_agent(session)

        result = agent.run("用两句话说明 RAG 和微调的区别，不用查资料", stream=False)

        print("\n=== 最终答复 ===")
        print(result.text)
        print(f"\n步数 {result.steps} · 工具 {result.tool_calls} 次 · {result.usage.total} tokens")

        # 会话可持久化恢复
        print(f"\n会话 ID：{session.id}（下次可用 scout --resume {session.id} 继续）")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()

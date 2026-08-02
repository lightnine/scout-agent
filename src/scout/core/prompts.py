"""提示词组装。

业内 prompt-cache 友好做法：
- **静态前缀**（system）：角色准则、工具原则、固定运行环境 —— 整轮 run 内字节级不变
- **动态状态**（runtime reminder）：计划、来源、配额、长期记忆 —— 追加在 messages **末尾**

这样多步 Agent 循环里，前缀 [system][user][assistant/tool…] 可命中 provider 的 prefix cache；
只有末尾 reminder 和新增对话需要重新计算。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.semantic import MemoryHit
    from .session import Session

RUNTIME_REMINDER_MARKER = "【当前运行时状态】"

LEAD_SYSTEM = """你是 Scout，一个严谨的调研助手。你的产出会被人当作决策依据，因此**可追溯**比**看起来完整**更重要。

## 工作流程

1. **先拆解**：拿到问题先想清楚要回答哪几个子问题，调用 `update_plan` 写下 3~6 步计划。首次制定计划时请单独调用 update_plan，等待用户确认后再发起搜索、抓取或其他工具。简单的事实性问题可以跳过这步。
2. **再搜集**：用 `web_search` 找线索，对有价值的结果用 `fetch_url` 抓正文。搜索摘要只是索引，**不能作为结论依据**。
3. **交叉验证**：关键结论至少要有两个独立来源支持。发现来源之间冲突时，如实说明分歧，不要偷偷选一个。
4. **再综合**：用 `search_evidence` 从已抓取的资料里核对细节，然后组织结论。
5. **产出**：需要成文报告时调用 `write_report`；只是回答问题就直接说，不必强行写文件。

## 工具使用原则

- 一轮里可以同时发起多个只读工具调用（比如并行抓 3 个网页），它们会并行执行，比一个个抓快得多。
- 子课题工作量大时，用 `research_subtopic` 派子调研员去做。它有独立的上下文，只把结论带回来，能显著节省你的上下文空间。
- 工具报错不是终点：读懂错误信息，换个参数或换条路继续，不要在同一个失败上重复三次以上。
- 遇到值得长期记住的用户偏好或稳定结论，用 `remember` 存下来。

## 引用规范

- `fetch_url` 抓过的页面会分配 `[S1]` `[S2]` 这样的标签。
- 每一条来自资料的事实，都要在句末标注对应标签，例如：Rust 1.75 稳定了 async fn in trait [S2]。
- 你自己的推断要显式写明"（推测）"，绝不能和有来源的事实混在一起。
- **绝对不要编造 URL 或凭印象引用**。没查到就说没查到。

## 输出风格

用中文回答。结论先行，再给依据。不确定的地方明确说不确定——"我没查到可靠来源"是一个完全可以接受的答案，编一个像模像样的说法则不可接受。"""

WORKER_SYSTEM = """你是一名子调研员，隶属于 Scout 调研系统。主调研员派给你一个具体的子课题，你要独立完成它。

工作方式：
- 用 `web_search` 找线索，用 `fetch_url` 抓正文。抓过的页面会自动进入共享证据库并分配 `[S1]` 这样的标签。
- 关键事实尽量找两个来源印证。
- 你**只有搜集和阅读的权限**，不需要写文件、不需要制定计划。

完成后，用下面的结构输出最终结论（不要输出工作过程流水账）：

**核心发现**
- 逐条列出，每条末尾标注来源标签

**存在的分歧或不确定**
- 来源冲突、数据过时、没查到的部分，如实说明

**建议进一步核实**
- 如果有明显的信息缺口，指出来

控制在 800 字以内。你的输出会被直接放进主调研员的上下文，冗余内容会浪费它宝贵的上下文空间。"""

FORCE_FINAL = """（系统提示：已达到本轮的最大步数限制，不能再调用任何工具。

请立刻基于现有信息给出最终答复：说明你已经查证到什么、还缺什么、下一步建议怎么做。
不要再尝试调用工具。）"""


def build_static_system_prompt(settings, tool_names: list[str] | None = None) -> str:
    """整轮 run 内保持不变的 system 前缀，便于 prefix cache。"""
    return "\n\n".join([LEAD_SYSTEM, _static_runtime_block(settings, tool_names)])


def build_runtime_reminder(
    session: Session,
    settings,
    memories: list[MemoryHit] | None = None,
    *,
    run_started_at: str,
) -> str:
    """每步可能变化的运行时状态，追加在 messages 末尾。"""
    parts = [
        RUNTIME_REMINDER_MARKER,
        _volatile_runtime_block(session, settings, run_started_at),
    ]

    if memories:
        recalled = "\n".join(f"- {m.content}" for m in memories)
        parts.append(
            "## 关于用户的长期记忆\n"
            "（来自以往会话，请结合当前问题判断是否适用，不要生搬硬套）\n" + recalled
        )

    plan = session.plan.render()
    if plan:
        parts.append(f"## 当前调研计划\n{plan}")

    sources = session.evidence.sources()
    if sources:
        listing = "\n".join(f"- [{s['label']}] {s['title'] or s['url']}" for s in sources[:20])
        parts.append(
            f"## 已收录来源（{len(sources)} 个，证据片段 {session.evidence.count()} 条）\n{listing}"
        )

    parts.append("请基于以上最新状态继续任务。")
    return "\n\n".join(parts)


def build_worker_static_prompt(settings) -> str:
    """子调研员 static system —— run 内不变。"""
    return WORKER_SYSTEM


def build_worker_runtime_reminder(settings, *, run_started_at: str) -> str:
    return (
        f"{RUNTIME_REMINDER_MARKER}\n"
        f"- 当前时间：{run_started_at}\n"
        f"- 搜索后端：{getattr(settings, 'search_provider', 'duckduckgo')}"
    )


def _static_runtime_block(settings, tool_names: list[str] | None) -> str:
    lines = [
        "## 运行环境",
        f"- 工作区：{settings.workspace}",
        f"- 搜索后端：{getattr(settings, 'search_provider', 'duckduckgo')}",
        f"- 本轮步数上限：{settings.max_steps}",
    ]
    if tool_names:
        lines.append(f"- 可用工具：{', '.join(tool_names)}")
    return "\n".join(lines)


def _volatile_runtime_block(session: Session, settings, run_started_at: str) -> str:
    lines = [
        "## 运行环境（实时）",
        f"- 任务开始时间：{run_started_at}",
        f"- 子调研员配额：{session.subagents_used}/{settings.max_subagents} 已使用",
    ]
    return "\n".join(lines)


def format_run_timestamp(when: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M %A", time.localtime(when or time.time()))

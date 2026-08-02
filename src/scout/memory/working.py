"""工作记忆：当前这轮任务的对话上下文，以及上下文压缩。

上下文窗口是 Agent 最稀缺的资源。一次调研跑几十步、抓十几个网页，
原始消息很快就会超窗。压缩的难点不在"叫模型总结一下"，
而在**不能把工具调用链切断**——见 ``_safe_split_index``。

system prompt 与 runtime reminder 由 Agent 每次调用前组装，不存放在这里。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..llm.base import LLMClient, Message

COMPACT_PROMPT = """请把下面这段 AI 调研助手的工作记录压缩成简洁的进展摘要，供它继续工作。

必须保留：
1. 用户的原始目标和明确约束
2. 已经查证的关键事实和结论（连同来源编号 [S1] 这样的引用标记）
3. 已经尝试过但走不通的方向（避免重复劳动）
4. 尚未完成的待办事项

必须丢弃：网页原文、冗长的工具输出、寒暄和过程性描述。

用中文输出，控制在 600 字以内，不要加任何开场白。

--- 工作记录开始 ---
{transcript}
--- 工作记录结束 ---"""


@dataclass(slots=True)
class CompactionResult:
    tokens_before: int
    tokens_after: int
    messages_dropped: int
    summary: str


@dataclass
class WorkingMemory:
    """一轮任务的消息序列（user / assistant / tool）。system 与 runtime reminder 由 Agent 组装。"""

    threshold: int = 16000
    keep_recent: int = 8
    messages: list[Message] = field(default_factory=list)

    def add(self, message: Message) -> None:
        self.messages.append(message)

    def extend(self, messages: list[Message]) -> None:
        self.messages.extend(messages)

    def clear(self) -> None:
        self.messages.clear()

    def tokens(self) -> int:
        return sum(m.token_estimate() for m in self.messages)

    def needs_compaction(self) -> bool:
        return self.tokens() > self.threshold

    def compact(self, llm: LLMClient) -> CompactionResult | None:
        """把早期消息压缩成一段摘要，返回压缩统计；无需压缩时返回 None。"""
        if not self.needs_compaction():
            return None
        split = self._safe_split_index()
        if split <= 1:
            return None

        before = self.tokens()
        head, tail = self.messages[:split], self.messages[split:]

        # 原始任务永远保留原文：摘要难免失真，目标一旦被改写整轮就偏了
        pinned = head[0] if head and head[0].role == "user" else None
        to_summarize = head[1:] if pinned else head
        if not to_summarize:
            return None

        summary = self._summarize(llm, to_summarize)
        rebuilt: list[Message] = []
        if pinned:
            rebuilt.append(pinned)
        rebuilt.append(
            Message(role="assistant", content=f"【前序工作摘要】\n{summary}")
        )
        rebuilt.append(
            Message(role="user", content="（系统提示：以上是压缩后的历史，请基于它继续任务）")
        )
        self.messages = rebuilt + tail

        return CompactionResult(
            tokens_before=before,
            tokens_after=self.tokens(),
            messages_dropped=len(to_summarize),
            summary=summary,
        )

    # ------------------------------------------------------------- 内部实现
    def _safe_split_index(self) -> int:
        """找一个可以安全切断的位置。

        约束：切点之后的第一条消息不能是 ``tool`` 角色——它靠 tool_call_id
        指向前面的 assistant 消息，一旦前半段被摘要掉，这个引用就悬空了，
        多数服务端会直接返回 400。所以切点要往后挪到非 tool 消息为止。
        """
        index = max(len(self.messages) - self.keep_recent, 0)
        while index < len(self.messages) and self.messages[index].role == "tool":
            index += 1
        return index

    @staticmethod
    def _summarize(llm: LLMClient, messages: list[Message]) -> str:
        transcript = "\n\n".join(_render(m) for m in messages)
        try:
            response = llm.chat(
                [Message(role="user", content=COMPACT_PROMPT.format(transcript=transcript))],
                temperature=0.1,
            )
            text = response.message.content.strip()
            if text:
                return text
        except Exception as exc:  # 压缩失败不能让整轮任务挂掉
            return f"（自动压缩失败：{exc}；以下为截断的原始记录）\n{transcript[-2000:]}"
        return transcript[-2000:]


def _render(message: Message) -> str:
    if message.role == "tool":
        return f"[工具结果 {message.name}]\n{message.content[:1500]}"
    if message.tool_calls:
        calls = ", ".join(f"{tc.name}({_brief(tc.arguments)})" for tc in message.tool_calls)
        return f"[助手调用工具] {calls}\n{message.content[:500]}"
    return f"[{message.role}] {message.content[:2000]}"


def _brief(arguments: dict) -> str:
    parts = []
    for key, value in arguments.items():
        text = str(value)
        parts.append(f"{key}={text[:60]}")
    return ", ".join(parts)

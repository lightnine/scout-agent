"""终端界面。

只做两件事：把事件流渲染成人看得懂的输出，把用户输入交给 Agent。
所有业务逻辑都在 core 里，这个文件随时可以换成 Web / 飞书机器人。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .approval import ApprovalAction, ApprovalDecision
from .config import load_settings
from .core.events import Event, EventBus, EventType
from .permissions import PolicyApprover
from .runtime import Runtime
from .tools.base import Risk, Tool
from .tools.search import resolve_provider

HELP = """[bold]可用命令[/bold]
  /new              开一个新会话（清空当前上下文）
  /sessions         列出历史会话
  /resume <id>      恢复某个历史会话
  /plan             查看当前调研计划
  /sources          查看已收录的来源
  /memory [关键词]  查看或检索长期记忆
  /tools            列出全部工具及其风险等级
  /trace [n]        查看最近 n 条执行事件
  /cost             查看本会话 token 消耗
  /help             显示本帮助
  /quit             退出

直接输入问题即可开始调研，例如：
  帮我调研一下 2026 年主流的开源向量数据库，对比它们的适用场景
"""


class Renderer:
    """把事件流渲染到终端。"""

    def __init__(self, console: Console, verbose: bool = False) -> None:
        self.console = console
        self.verbose = verbose
        self._streaming = False

    def handle(self, event: Event) -> None:
        kind = event.type
        data = event.data
        worker = event.agent != "main"
        tag = f"[dim]({event.agent})[/dim] " if worker else ""

        if kind is EventType.LLM_DELTA:
            if not worker:
                self.console.print(data.get("text", ""), end="", markup=False, highlight=False)
                self._streaming = True
            return

        if kind is EventType.LLM_END:
            if self._streaming:
                self.console.print()
                self._streaming = False
            elif worker and data.get("content") and self.verbose:
                self.console.print(f"{tag}[dim]{_clip(data['content'], 100)}[/dim]")
            return

        if kind is EventType.TOOL_START:
            args = _format_args(data.get("arguments", {}))
            self.console.print(f"{tag}[cyan]⚙ {data['tool']}[/cyan][dim]({args})[/dim]")
            return

        if kind is EventType.TOOL_END:
            icon = "[green]✓[/green]" if data.get("ok") else "[red]✗[/red]"
            cost = data.get("duration_ms", 0)
            self.console.print(
                f"{tag}  {icon} [dim]{_escape(data.get('display', ''))} ({cost}ms)[/dim]"
            )
            return

        if kind is EventType.MEMORY_RECALL:
            self.console.print(f"[magenta]🧠 召回 {data['count']} 条长期记忆[/magenta]")
            if self.verbose:
                for item in data.get("items", []):
                    self.console.print(f"   [dim]- {_escape(_clip(item, 80))}[/dim]")
            return

        if kind is EventType.COMPACTION:
            self.console.print(
                f"[yellow]📦 上下文压缩：{data['tokens_before']} → {data['tokens_after']} tokens"
                f"（合并 {data['dropped']} 条消息）[/yellow]"
            )
            return

        if kind is EventType.SUBAGENT_START:
            self.console.print(
                f"[blue]🚀 派出子调研员 {data['worker']}：{_escape(_clip(data['brief'], 60))}[/blue]"
            )
            return

        if kind is EventType.SUBAGENT_END:
            self.console.print(
                f"[blue]🏁 {data['worker']} 完成（{data['steps']} 步，{data['tokens']} tokens）[/blue]"
            )
            return

        if kind is EventType.ERROR:
            self.console.print(f"[red]错误：{_escape(str(data.get('error')))}[/red]")
            return

        if kind is EventType.STEP_START and self.verbose and not worker:
            self.console.print(f"[dim]── 第 {data['step']}/{data['max']} 步 ──[/dim]")


def make_approver(console: Console, mode: str) -> PolicyApprover:
    class _CliGateway:
        def request(self, request, emit=None):
            tool = Tool(
                name=request.payload["tool"],
                description="",
                parameters={},
                fn=lambda: None,
                risk=Risk(request.payload["risk"]),
            )
            args = request.payload["arguments"]
            console.print()
            console.print(
                Panel(
                    f"[bold]{tool.name}[/bold]  风险等级：{_risk_label(tool.risk)}\n\n"
                    f"{_escape(_format_args(args, limit=200))}",
                    title="需要你确认",
                    border_style="yellow",
                )
            )
            answer = console.input(
                "[yellow]执行吗？(y=同意 / n=拒绝 / a=本会话都同意) [/yellow]"
            ).strip().lower()
            if answer == "a":
                return ApprovalDecision(ApprovalAction.ALLOW_SESSION)
            if answer in ("y", "yes", ""):
                return ApprovalDecision(ApprovalAction.APPROVE)
            return ApprovalDecision(ApprovalAction.REJECT)

    return PolicyApprover(mode, gateway=_CliGateway())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scout", description="Scout：会自己查资料、给引用的调研 Agent"
    )
    parser.add_argument("question", nargs="*", help="直接提问（单次执行模式）")
    parser.add_argument("--workspace", "-w", default=".", help="工作区目录")
    parser.add_argument("--resume", "-r", metavar="ID", help="恢复指定会话")
    parser.add_argument("--auto", action="store_true", help="自动批准所有操作（无人值守）")
    parser.add_argument("--readonly", action="store_true", help="只读模式，禁止任何写操作")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示更详细的过程")
    parser.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    args = parser.parse_args(argv)

    console = Console()
    settings = load_settings(Path(args.workspace))
    if args.auto:
        settings.permission_mode = "auto"
    if args.readonly:
        settings.permission_mode = "readonly"

    try:
        settings.validate()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    _, provider_notice = resolve_provider(settings)
    if provider_notice:
        console.print(f"[yellow]{provider_notice.strip()}[/yellow]")

    bus = EventBus()
    bus.subscribe(Renderer(console, args.verbose).handle)
    runtime = Runtime(settings, bus=bus, approver=make_approver(console, settings.permission_mode))

    try:
        session = (
            runtime.resume_session(args.resume) if args.resume else runtime.new_session()
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    agent = runtime.build_agent(session)

    stream = not args.no_stream
    try:
        if args.question:
            _ask(console, agent, " ".join(args.question), stream)
            return 0
        return _repl(console, runtime, agent, session, stream)
    finally:
        runtime.close()


def _repl(console: Console, runtime: Runtime, agent, session, stream: bool) -> int:
    console.print(
        Panel(
            f"[bold cyan]Scout[/bold cyan] 调研助手\n"
            f"模型 {runtime.settings.model} · 搜索 {resolve_provider(runtime.settings)[0]} · "
            f"权限 {runtime.settings.permission_mode}\n"
            f"会话 {session.id} · 输入 [bold]/help[/bold] 查看命令",
            border_style="cyan",
        )
    )
    while True:
        try:
            line = console.input("\n[bold green]›[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n再见。")
            return 0
        if not line:
            continue

        if line.startswith("/"):
            command, _, rest = line[1:].partition(" ")
            if command in ("quit", "exit", "q"):
                console.print("再见。")
                return 0
            if command == "new":
                session = runtime.new_session()
                agent = runtime.build_agent(session)
                console.print(f"[green]已开启新会话 {session.id}[/green]")
                continue
            if command == "resume":
                try:
                    session = runtime.resume_session(rest.strip())
                except ValueError as exc:
                    console.print(f"[red]{exc}[/red]")
                    continue
                agent = runtime.build_agent(session)
                console.print(
                    f"[green]已恢复会话 {session.id}（{len(session.working.messages)} 条消息）[/green]"
                )
                continue
            _handle_command(console, runtime, agent, session, command, rest.strip())
            continue

        _ask(console, agent, line, stream)


def _handle_command(console: Console, runtime: Runtime, agent, session, command: str, rest: str) -> None:
    if command == "help":
        console.print(HELP)
    elif command == "sessions":
        table = Table("会话 ID", "标题", "消息数", title="历史会话")
        for row in runtime.list_sessions():
            table.add_row(row["id"], row["title"] or "(未命名)", str(row["message_count"]))
        console.print(table)
    elif command == "plan":
        console.print(session.plan.render() or "[dim]当前没有计划[/dim]")
    elif command == "sources":
        sources = session.evidence.sources()
        if not sources:
            console.print("[dim]还没有收录任何来源[/dim]")
        else:
            table = Table("标签", "标题", "URL", title=f"共 {len(sources)} 个来源")
            for s in sources:
                table.add_row(s["label"], _clip(s["title"] or "-", 40), _clip(s["url"], 60))
            console.print(table)
    elif command == "memory":
        items = (
            [(h.content, h.tags) for h in runtime.memory.search(rest, limit=20)]
            if rest
            else [(m["content"], m["tags"]) for m in runtime.memory.all()]
        )
        if not items:
            console.print("[dim]长期记忆是空的[/dim]")
        else:
            for content, tags in items:
                suffix = f" [dim]#{tags}[/dim]" if tags else ""
                console.print(f"- {_escape(content)}{suffix}")
    elif command == "tools":
        table = Table("工具", "风险", "说明", title="已注册工具")
        for tool in agent.registry.tools:
            table.add_row(
                tool.name, _risk_label(tool.risk), _clip(tool.description.splitlines()[0], 60)
            )
        console.print(table)
    elif command == "trace":
        limit = int(rest) if rest.isdigit() else 20
        records = runtime.trace.tail(limit) if runtime.trace else []
        for record in records:
            console.print(f"[dim]{record.get('type'):<14}[/dim] {_escape(_summarize(record))}")
    elif command == "cost":
        usage = session.usage
        console.print(
            f"本会话：{usage.calls} 次模型调用，"
            f"输入 {usage.prompt_tokens} / 输出 {usage.completion_tokens} tokens"
        )
    else:
        console.print(f"[red]未知命令 /{command}，输入 /help 查看帮助[/red]")


def _ask(console: Console, agent, question: str, stream: bool) -> None:
    console.print()
    result = agent.run(question, stream=stream)
    if not stream and result.text:
        console.print(Markdown(result.text))
    console.print(
        f"\n[dim]— {result.steps} 步 · {result.tool_calls} 次工具调用 · "
        f"{result.usage.total} tokens · {result.stop_reason}[/dim]"
    )


def _risk_label(risk: Risk) -> str:
    return {
        Risk.SAFE: "[green]只读[/green]",
        Risk.CAUTION: "[yellow]有副作用[/yellow]",
        Risk.DANGEROUS: "[red]危险[/red]",
    }[risk]


def _format_args(arguments: dict[str, Any], limit: int = 80) -> str:
    parts = []
    for key, value in arguments.items():
        text = str(value).replace("\n", " ")
        parts.append(f"{key}={text[:limit]}{'…' if len(text) > limit else ''}")
    return ", ".join(parts)


def _summarize(record: dict[str, Any]) -> str:
    keys = ("tool", "display", "step", "worker", "content", "stop_reason", "count")
    return " ".join(f"{k}={_clip(str(record[k]), 60)}" for k in keys if k in record)


def _clip(text: str, limit: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _escape(text: str) -> str:
    """避免工具输出里的方括号被 rich 当成标记解析。"""
    return text.replace("[", r"\[")


if __name__ == "__main__":
    sys.exit(main())

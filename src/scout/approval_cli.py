from __future__ import annotations

from rich.panel import Panel

from .approval import ApprovalAction, ApprovalDecision, ApprovalKind, ApprovalRequest


class CliApprovalGateway:
    def __init__(self, console) -> None:
        self.console = console

    def request(self, request: ApprovalRequest, emit=None) -> ApprovalDecision:
        if emit:
            emit("approval_required", request.event_data())

        def finish(decision: ApprovalDecision) -> ApprovalDecision:
            if emit:
                emit(
                    "approval_resolved",
                    {
                        "approval_id": request.id,
                        "run_id": request.run_id,
                        "session_id": request.session_id,
                        "action": decision.action.value,
                    },
                )
            return decision

        body = request.payload.get("plan") or self._tool_body(request.payload)
        self.console.print()
        self.console.print(Panel(str(body), title=request.title, border_style="yellow"))
        if request.kind is ApprovalKind.PLAN:
            answer = self.console.input(
                "[yellow](y=确认 / e=修改意见 / c=取消运行) [/yellow]"
            ).strip().lower()
            if answer == "e":
                feedback = self.console.input("[yellow]修改意见：[/yellow]").strip()
                return finish(ApprovalDecision(ApprovalAction.REVISE, feedback))
            if answer == "c":
                return finish(ApprovalDecision(ApprovalAction.CANCEL))
            return finish(ApprovalDecision(ApprovalAction.APPROVE))

        answer = self.console.input(
            "[yellow](y=允许一次 / n=拒绝 / a=本会话允许 / c=取消运行) [/yellow]"
        ).strip().lower()
        if answer == "a":
            return finish(ApprovalDecision(ApprovalAction.ALLOW_SESSION))
        if answer == "c":
            return finish(ApprovalDecision(ApprovalAction.CANCEL))
        if answer in {"n", "no"}:
            return finish(ApprovalDecision(ApprovalAction.REJECT))
        return finish(ApprovalDecision(ApprovalAction.APPROVE))

    @staticmethod
    def _tool_body(payload: dict) -> str:
        return f"{payload.get('tool', '')}\n{payload.get('arguments', {})}"

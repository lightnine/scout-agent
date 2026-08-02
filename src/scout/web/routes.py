"""REST and server-sent event routes for the web workbench."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..approval import ApprovalDecision
from .gateway import ApprovalAlreadyResolvedError, ApprovalNotFoundError
from .run_manager import ActiveRunError, RunNotFoundError
from .schemas import (
    ApprovalSubmitRequest,
    CancelRunResponse,
    CreateRunRequest,
    CreateRunResponse,
    CreateSessionRequest,
    HealthResponse,
    SessionDetail,
    SessionSummary,
)
from .sse import stream_events


def build_router() -> APIRouter:
    """Build API routes that retrieve dependencies from application state."""
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> dict[str, str | None]:
        active = request.app.state.run_manager.active
        return {"status": "ok", "active_run_id": active.run_id if active else None}

    @router.get("/sessions", response_model=list[SessionSummary])
    def list_sessions(request: Request) -> list[dict]:
        return request.app.state.runtime.list_sessions()

    @router.post("/sessions", status_code=201, response_model=SessionSummary)
    def create_session(body: CreateSessionRequest, request: Request) -> dict:
        runtime = request.app.state.runtime
        session = runtime.new_session(body.title)
        return runtime.store.get_session(session.id)

    @router.get("/sessions/{session_id}", response_model=SessionDetail)
    def get_session(session_id: str, request: Request) -> dict:
        runtime = request.app.state.runtime
        manager = request.app.state.run_manager
        try:
            snapshot = runtime.session_snapshot(session_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

        active = manager.active
        if active and active.session_id == session_id:
            snapshot["active_run_id"] = active.run_id
            snapshot["run_status"] = active.status
        else:
            snapshot["active_run_id"] = None
            snapshot["run_status"] = "idle"
        return snapshot

    @router.post("/sessions/{session_id}/runs", status_code=202, response_model=CreateRunResponse)
    def start_run(session_id: str, body: CreateRunRequest, request: Request) -> dict[str, str]:
        runtime = request.app.state.runtime
        if runtime.store.get_session(session_id) is None:
            raise HTTPException(404, f"找不到会话 {session_id}")
        try:
            record = request.app.state.run_manager.start_run(session_id, body.question)
        except ActiveRunError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"run_id": record.run_id, "session_id": record.session_id}

    @router.get("/runs/{run_id}/events")
    def run_events(
        run_id: str,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        manager = request.app.state.run_manager
        try:
            manager.get(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(404, f"找不到运行 {run_id}") from exc

        try:
            after_id = int(last_event_id or 0)
            if after_id < 0:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID 必须是非负整数") from exc

        return StreamingResponse(
            stream_events(manager, run_id, after_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/runs/{run_id}/cancel", response_model=CancelRunResponse)
    def cancel_run(run_id: str, request: Request) -> dict[str, str]:
        try:
            status = request.app.state.run_manager.cancel(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(404, f"找不到运行 {run_id}") from exc
        return {"run_id": run_id, "status": status}

    @router.post("/approvals/{approval_id}", status_code=202)
    def resolve_approval(
        approval_id: str,
        body: ApprovalSubmitRequest,
        request: Request,
    ) -> dict[str, str]:
        try:
            request.app.state.gateway.resolve(
                approval_id,
                ApprovalDecision(body.action, body.feedback),
            )
        except ApprovalNotFoundError as exc:
            raise HTTPException(404, f"找不到审批 {approval_id}") from exc
        except ApprovalAlreadyResolvedError as exc:
            raise HTTPException(409, f"审批 {approval_id} 已处理") from exc
        return {"approval_id": approval_id, "status": "accepted"}

    return router

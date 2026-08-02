"""Pydantic request and response models for the web API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..approval import ApprovalAction


class CreateSessionRequest(BaseModel):
    title: str = ""


class CreateRunRequest(BaseModel):
    question: str = Field(min_length=1)


class CreateRunResponse(BaseModel):
    run_id: str
    session_id: str


class ApprovalSubmitRequest(BaseModel):
    action: ApprovalAction
    feedback: str = ""


class CancelRunResponse(BaseModel):
    run_id: str
    status: Literal["cancelling", "already_finished"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    active_run_id: str | None = None


class SessionSummary(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: float
    updated_at: float


class SessionDetail(BaseModel):
    id: str
    title: str
    messages: list[dict[str, Any]]
    plan: str
    plan_steps: list[str]
    plan_current: int
    sources: list[dict[str, Any]]
    usage: dict[str, int]
    active_run_id: str | None = None
    run_status: str = "idle"

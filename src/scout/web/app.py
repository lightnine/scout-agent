"""FastAPI application factory for Scout's web workbench."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ..config import Settings, load_settings
from ..runtime import Runtime
from .gateway import WebApprovalGateway
from .routes import build_router
from .run_manager import RunManager


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    """Create a web application with one runtime and run manager."""
    workspace = Path(os.getenv("SCOUT_WORKSPACE", "."))
    settings = settings or load_settings(workspace)
    gateway = WebApprovalGateway()
    runtime = runtime or Runtime(settings, approval_gateway=gateway)

    # An injected Runtime may have been assembled for non-web use. Attach the
    # web gateway before any run starts so requests can unblock its approver.
    runtime.approval_gateway = gateway
    if hasattr(runtime.approver, "gateway"):
        runtime.approver.gateway = gateway

    manager = RunManager(runtime, gateway)
    closed = False

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal closed
        app.state.runtime = runtime
        app.state.gateway = gateway
        app.state.run_manager = manager
        try:
            yield
        finally:
            if not closed:
                manager.shutdown()
                runtime.close()
                closed = True

    app = FastAPI(title="Scout", lifespan=lifespan)
    app.include_router(build_router(), prefix="/api")
    return app

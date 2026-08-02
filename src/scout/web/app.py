"""FastAPI application factory for Scout's web workbench."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from ..config import Settings, load_settings
from ..runtime import Runtime
from .gateway import WebApprovalGateway
from .routes import build_router
from .run_manager import RunManager


def _find_static_dir() -> Path | None:
    """Locate source assets in a checkout or packaged assets in an installed wheel."""
    source_static = Path(__file__).parents[3] / "web" / "dist"
    packaged_static = Path(__file__).with_name("static")
    for candidate in (source_static, packaged_static):
        if (candidate / "index.html").is_file():
            return candidate
    return None


class SPAStaticFiles(StaticFiles):
    """Serve frontend files and fall back to index only for extensionless SPA routes."""

    async def get_response(self, path: str, scope: dict) -> Response:
        if path.lstrip("/").partition("/")[0] == "api":
            raise StarletteHTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not self._is_spa_route(path, scope):
                raise
            return await super().get_response("index.html", scope)

        if response.status_code == 404 and self._is_spa_route(path, scope):
            return await super().get_response("index.html", scope)
        return response

    @staticmethod
    def _is_spa_route(path: str, scope: dict) -> bool:
        first_segment = path.lstrip("/").partition("/")[0]
        return (
            scope.get("method") in {"GET", "HEAD"}
            and first_segment not in {"api", "assets"}
            and not Path(path).suffix
        )


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
                closed = True

    app = FastAPI(title="Scout", lifespan=lifespan)
    app.include_router(build_router(), prefix="/api")
    static_dir = _find_static_dir()
    if static_dir is not None:
        app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="frontend")
    return app

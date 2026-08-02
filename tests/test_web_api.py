import threading
import time

from conftest import FakeLLM
from fastapi.testclient import TestClient

from scout.approval import ApprovalAction, ApprovalDecision, ApprovalKind, ApprovalRequest
from scout.runtime import Runtime
from scout.web.app import create_app


def make_client(settings, script=None, llm=None):
    runtime = Runtime(settings, llm=llm or FakeLLM(script), enable_trace=False)
    return TestClient(create_app(settings=settings, runtime=runtime))


def test_health_and_session_creation(settings):
    with make_client(settings) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "active_run_id": None}

        created = client.post("/api/sessions", json={"title": "Research"})
        assert created.status_code == 201
        assert created.json()["title"] == "Research"

        detail = client.get(f"/api/sessions/{created.json()['id']}")
        assert detail.status_code == 200
        assert detail.json()["title"] == "Research"
        assert detail.json()["run_status"] == "idle"


def test_second_active_run_returns_409(settings):
    class BlockingLLM(FakeLLM):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def chat(self, *args, **kwargs):
            self.started.set()
            self.release.wait(2)
            return super().chat(*args, **kwargs)

    llm = BlockingLLM()
    with make_client(settings, llm=llm) as client:
        session = client.post("/api/sessions", json={}).json()
        first = client.post(f"/api/sessions/{session['id']}/runs", json={"question": "one"})
        assert first.status_code == 202
        assert llm.started.wait(1)

        second = client.post(f"/api/sessions/{session['id']}/runs", json={"question": "two"})
        assert second.status_code == 409
        llm.release.set()


def test_events_replay_safely_and_include_streaming_headers(settings):
    with make_client(settings) as client:
        session = client.post("/api/sessions", json={}).json()
        run = client.post(f"/api/sessions/{session['id']}/runs", json={"question": "one"}).json()

        deadline = time.monotonic() + 1
        while client.app.state.run_manager.get(run["run_id"]).status != "finished":
            assert time.monotonic() < deadline
            time.sleep(0.01)

        response = client.get(f"/api/runs/{run['run_id']}/events")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        assert "event: run_start" in response.text

        replay = client.get(
            f"/api/runs/{run['run_id']}/events",
            headers={"Last-Event-ID": "1"},
        )
        assert "\nid: 1\n" not in replay.text

        bad_cursor = client.get(
            f"/api/runs/{run['run_id']}/events",
            headers={"Last-Event-ID": "not-a-number"},
        )
        assert bad_cursor.status_code == 400


def test_injected_runtime_uses_web_gateway_for_approval_resolution(settings):
    runtime = Runtime(settings, llm=FakeLLM(), enable_trace=False)
    app = create_app(settings=settings, runtime=runtime)

    with TestClient(app) as client:
        gateway = client.app.state.gateway
        assert runtime.approval_gateway is gateway
        assert runtime.approver.gateway is gateway

        request = ApprovalRequest.create("run-1", "session-1", ApprovalKind.PLAN, "Plan", {})
        result = {}
        thread = threading.Thread(
            target=lambda: result.setdefault("decision", gateway.request(request)),
            daemon=True,
        )
        thread.start()

        deadline = time.monotonic() + 1
        while not gateway.pending_for_run("run-1"):
            assert time.monotonic() < deadline
            time.sleep(0.01)

        resolved = client.post(
            f"/api/approvals/{request.id}",
            json={"action": ApprovalAction.APPROVE.value},
        )
        assert resolved.status_code == 202
        thread.join(1)
        assert result["decision"] == ApprovalDecision(ApprovalAction.APPROVE)

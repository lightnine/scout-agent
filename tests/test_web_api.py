import json
import threading
import time
from types import SimpleNamespace

import pytest
from conftest import FakeLLM
from fastapi.testclient import TestClient

from scout.approval import ApprovalAction, ApprovalDecision, ApprovalKind, ApprovalRequest
from scout.core.events import EventBus
from scout.runtime import Runtime
from scout.web.app import create_app
from scout.web.sse import stream_events
from scout.web_cli import main


def make_client(settings, script=None, llm=None, raise_server_exceptions=True):
    runtime = Runtime(settings, llm=llm or FakeLLM(script), enable_trace=False)
    return TestClient(
        create_app(settings=settings, runtime=runtime),
        raise_server_exceptions=raise_server_exceptions,
    )


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


@pytest.mark.parametrize("last_event_id", ["", " ", "\t", "+1", "-1", "1.0", "one"])
def test_events_reject_non_decimal_last_event_id(settings, last_event_id):
    with make_client(settings) as client:
        session = client.post("/api/sessions", json={}).json()
        run = client.post(f"/api/sessions/{session['id']}/runs", json={"question": "one"}).json()

        response = client.get(
            f"/api/runs/{run['run_id']}/events",
            headers={"Last-Event-ID": last_event_id},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Last-Event-ID 必须是非负十进制整数"


def test_events_reject_oversized_decimal_last_event_id(settings):
    with make_client(settings, raise_server_exceptions=False) as client:
        session = client.post("/api/sessions", json={}).json()
        run = client.post(f"/api/sessions/{session['id']}/runs", json={"question": "one"}).json()

        response = client.get(
            f"/api/runs/{run['run_id']}/events",
            headers={"Last-Event-ID": "9" * 5_000},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Last-Event-ID 必须是非负十进制整数"


def test_unknown_resources_return_404(settings):
    with make_client(settings) as client:
        assert client.get("/api/sessions/missing").status_code == 404
        assert client.post("/api/sessions/missing/runs", json={"question": "one"}).status_code == 404
        assert client.get("/api/runs/missing/events").status_code == 404
        assert client.post("/api/runs/missing/cancel").status_code == 404
        assert client.post(
            "/api/approvals/missing",
            json={"action": ApprovalAction.APPROVE.value},
        ).status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/sessions", {"title": 1}),
        ("post", "/api/sessions/missing/runs", {}),
        ("post", "/api/sessions/missing/runs", {"question": ""}),
        ("post", "/api/approvals/missing", {"action": "invalid"}),
    ],
)
def test_request_body_validation_returns_422(settings, method, path, body):
    with make_client(settings) as client:
        response = getattr(client, method)(path, json=body)

    assert response.status_code == 422


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


def test_events_replay_sse_envelopes_and_include_streaming_headers(settings):
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
        frames = [frame for frame in response.text.strip().split("\n\n") if frame]
        first = dict(line.split(": ", 1) for line in frames[0].splitlines())
        assert first["id"] == "1"
        assert first["event"] == "run_start"
        assert json.loads(first["data"]) == {
            "id": 1,
            "type": "run_start",
            "run_id": run["run_id"],
            "session_id": session["id"],
            "ts": pytest.approx(json.loads(first["data"])["ts"]),
            "agent": "main",
            "data": {"run_id": run["run_id"], "input": "one"},
        }

        replay = client.get(
            f"/api/runs/{run['run_id']}/events",
            headers={"Last-Event-ID": "1"},
        )
        replay_frames = [frame for frame in replay.text.strip().split("\n\n") if frame]
        replay_ids = [int(frame.splitlines()[0].removeprefix("id: ")) for frame in replay_frames]
        assert replay_ids == list(range(2, len(frames) + 1))


def test_stream_events_yields_heartbeat_then_stops_after_completion():
    record = SimpleNamespace(status="running")

    class HeartbeatManager:
        def events_after(self, run_id, after_id):
            return []

        def get(self, run_id):
            return record

        def wait_for_events(self, run_id, after_id, timeout):
            record.status = "finished"
            return False

    events = stream_events(HeartbeatManager(), "run-1")
    assert next(events) == ": keep-alive\n\n"
    with pytest.raises(StopIteration):
        next(events)


def test_cancel_known_run_returns_cancelling(settings):
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
        run = client.post(f"/api/sessions/{session['id']}/runs", json={"question": "one"}).json()
        assert llm.started.wait(1)

        response = client.post(f"/api/runs/{run['run_id']}/cancel")
        assert response.status_code == 200
        assert response.json() == {"run_id": run["run_id"], "status": "cancelling"}
        llm.release.set()


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

        duplicate = client.post(
            f"/api/approvals/{request.id}",
            json={"action": ApprovalAction.APPROVE.value},
        )
        assert duplicate.status_code == 409


def test_lifespan_closes_manager_then_runtime_once(settings, monkeypatch):
    calls = []

    class RecordingManager:
        def __init__(self, runtime, gateway):
            self.active = None
            calls.append(("manager_init", runtime, gateway))

        def shutdown(self):
            calls.append(("manager_shutdown",))

    class RecordingRuntime:
        def __init__(self):
            self.bus = EventBus()
            self.approval_gateway = None
            self.approver = SimpleNamespace(gateway=None)

        def close(self):
            calls.append(("runtime_close",))

    monkeypatch.setattr("scout.web.app.RunManager", RecordingManager)
    app = create_app(settings=settings, runtime=RecordingRuntime())

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    assert [call[0] for call in calls] == ["manager_init", "manager_shutdown", "runtime_close"]


def test_scout_web_forwards_defaults_without_starting_server(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("scout.web_cli.uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))

    assert main([]) == 0

    assert calls == [
        (
            ("scout.web.app:create_app",),
            {"factory": True, "host": "127.0.0.1", "port": 8080, "reload": False},
        )
    ]
    assert main(["-w", str(tmp_path), "--host", "0.0.0.0", "--port", "9000", "--reload"]) == 0
    assert calls[1] == (
        ("scout.web.app:create_app",),
        {"factory": True, "host": "0.0.0.0", "port": 9000, "reload": True},
    )
    assert __import__("os").environ["SCOUT_WORKSPACE"] == str(tmp_path.resolve())

from pathlib import Path

from starlette.testclient import TestClient

from loommux.mcp.control import _resource_action
from loommux.mcp.server import create_mcp
from loommux.resource import ResourceProvisionError


def test_http_control_plane_exposes_console_and_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_mcp().http_app(path="/mcp")

    with TestClient(app) as client:
        console = client.get("/")
        policy = client.get("/api/lease-policy")
        resources = client.get("/api/resources")
        updated = client.put(
            "/api/lease-policy",
            json={
                "mode": "heartbeat",
                "heartbeat_interval_seconds": 2,
                "heartbeat_timeout_seconds": 8,
            },
        )

    assert console.status_code == 200
    assert "Loommux Kernel Resources" in console.text
    assert policy.json()["policy"]["generation"] == 1
    assert resources.json()["resources"] == []
    assert updated.json()["changed"] is True
    assert updated.json()["policy"]["generation"] == 2


def test_http_control_plane_switches_to_the_connection_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_mcp().http_app(path="/mcp")

    with TestClient(app) as client:
        initial = client.get("/api/lease-policy").json()["policy"]
        updated = client.put(
            "/api/lease-policy",
            json={"mode": "connection", "connection_grace_seconds": 45},
        ).json()

    assert initial["connection_grace_seconds"] == 60
    assert updated["policy"]["mode"] == "connection"
    assert updated["policy"]["connection_grace_seconds"] == 45
    assert updated["policy"]["heartbeat_timeout_seconds"] == initial["heartbeat_timeout_seconds"]


def test_console_reads_the_connection_lease_contract() -> None:
    """The console is plain HTML; this pins the field names it shares with the API."""
    console = (Path(__file__).parents[2] / "src/loommux/mcp/console.html").read_text(encoding="utf-8")

    assert '<option value="connection">connection</option>' in console
    assert 'id="grace"' in console
    for field in ("connection_grace_seconds", "l.standing", "l.alert", "l.grace_remaining_seconds", "r.kernel_state", "r.held_by", 'id="signal"'):
        assert field in console


def test_program_routes_validate_requests_before_reaching_a_kernel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_mcp().http_app(path="/mcp")

    with TestClient(app) as client:
        responses = {
            "unknown resource": client.get("/api/resources/missing"),
            "not an object": client.post("/api/resources/ensure", content=b"[1]"),
            "not json": client.post("/api/resources/ensure", content=b"{"),
            "name type": client.post("/api/resources/ensure", json={"name": 3}),
            "unprintable name": client.post("/api/resources/ensure", json={"name": "a\nb", "holder": None}),
            "no holder": client.post("/api/resources/ensure", json={"name": "lab", "holder": None}),
            "missing holder": client.post("/api/resources/ensure", json={"name": "lab", "holder": "gone"}),
            "source type": client.post("/api/resources/missing/executions", json={"source": 1}),
            "wait type": client.post("/api/resources/missing/executions", json={"source": "1", "wait_seconds": True}),
            "submit body": client.post("/api/resources/missing/executions", content=b"null"),
            "submit target": client.post("/api/resources/missing/executions", json={"source": "1"}),
            "status target": client.get("/api/resources/missing/executions/1"),
            "wait body": client.post("/api/resources/missing/executions/1/wait", content=b"x"),
            "timeout type": client.post("/api/resources/missing/executions/1/wait", json={"timeout_seconds": "soon"}),
            "output target": client.get("/api/resources/missing/executions/1/output"),
            "follow params": client.get("/api/resources/missing/executions/1/combined?since=later"),
            "follow target": client.get("/api/resources/missing/executions/1/combined?since=0&wait=0.1"),
        }

    assert {name: response.status_code for name, response in responses.items()} == {
        "unknown resource": 404,
        "not an object": 400,
        "not json": 400,
        "name type": 400,
        "unprintable name": 400,
        "no holder": 404,
        "missing holder": 404,
        "source type": 400,
        "wait type": 400,
        "submit body": 400,
        "submit target": 404,
        "status target": 404,
        "wait body": 400,
        "timeout type": 400,
        "output target": 404,
        "follow params": 400,
        "follow target": 404,
    }
    assert responses["no holder"].json()["message"] == "no kernel named 'lab'; only a kernel can open one"


def test_http_control_plane_rejects_invalid_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_mcp().http_app(path="/mcp")

    with TestClient(app) as client:
        response = client.put(
            "/api/lease-policy",
            json={
                "heartbeat_interval_seconds": 10,
                "heartbeat_timeout_seconds": 5,
            },
        )

    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_http_control_plane_requires_boolean_force(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_mcp().http_app(path="/mcp")

    with TestClient(app) as client:
        response = client.post(
            "/api/resources/missing/recycle",
            json={"force": "false"},
        )

    assert response.status_code == 400
    assert response.json()["message"] == "force must be a boolean"


async def test_resource_action_preserves_manager_and_operation_failures() -> None:
    async def failed_result():
        return {"ok": False, "status": "kernel_not_started"}

    async def failed_manager():
        raise ResourceProvisionError("restart failed")

    result_response = await _resource_action(failed_result)
    manager_response = await _resource_action(failed_manager)

    assert result_response.status_code == 409
    assert manager_response.status_code == 409

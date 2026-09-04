from pathlib import Path

from starlette.testclient import TestClient

from loommux.mcp.server import create_mcp


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

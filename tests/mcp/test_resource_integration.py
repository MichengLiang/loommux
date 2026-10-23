import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from server_harness import reserve_port, running_server, wait_for_resource_count

from loommux.client import LeaseAwareClient


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="external Streamable HTTP subprocess harness is Linux-only",
)
@pytest.mark.timeout(60)
def test_http_resources_are_private_shareable_and_lease_reclaimed(
    tmp_path: Path,
) -> None:
    async def scenario(origin: str) -> None:
        server_url = f"{origin}/mcp"

        async with LeaseAwareClient(server_url, "private-a") as first:
            first_result = await first.call_tool(
                "run_cell",
                {"freeform": "private_value = 7\nprint(private_value)"},
            )
            assert first_result.content[0].text == "In [1]:\n7\n"

        async with LeaseAwareClient(server_url, "private-b") as second:
            isolated = await second.call_tool(
                "run_cell",
                {"freeform": "print(globals().get('private_value'))"},
            )
            assert isolated.content[0].text == "In [1]:\nNone\n"

        async with LeaseAwareClient(
            server_url,
            "named-a",
            resource_name="shared-lab",
        ) as first_shared:
            await first_shared.call_tool(
                "run_cell",
                {"freeform": "shared_value = 11"},
            )

        async with LeaseAwareClient(
            server_url,
            "named-b",
            resource_name="shared-lab",
        ) as second_shared:
            shared = await second_shared.call_tool(
                "run_cell",
                {"freeform": "print(shared_value)"},
            )
            assert shared.content[0].text == "In [2]:\n11\n"

        async with LeaseAwareClient(
            server_url,
            "persistent",
            resource_name="heartbeat-lab",
        ) as persistent:
            first = await persistent.call_tool(
                "run_cell",
                {"freeform": "print('first')"},
            )
            await asyncio.sleep(0.35)
            second = await persistent.call_tool(
                "run_cell",
                {"freeform": "print('second')"},
            )
            assert first.content[0].text == "In [1]:\nfirst\n"
            assert second.content[0].text == "In [2]:\nsecond\n"
            assert persistent.heartbeat_count >= 4

        await wait_for_resource_count(origin, 0)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace) as origin:
        asyncio.run(scenario(origin))


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="external Streamable HTTP subprocess harness is Linux-only",
)
@pytest.mark.timeout(60)
def test_open_standalone_stream_holds_a_connection_lease_past_every_window(
    tmp_path: Path,
) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "connected") as client:
            await client.call_tool("run_cell", {"freeform": "kept = 5"})
            # Idle well past both the 0.2 s activity window and the 0.3 s
            # grace; only the open GET stream can keep this lease.
            await asyncio.sleep(0.6)
            async with httpx.AsyncClient(timeout=1) as http:
                [resource] = (await http.get(f"{origin}/api/resources")).json()["resources"]
            kept = await client.call_tool("run_cell", {"freeform": "print(kept)"})
            assert client.heartbeat_count == 0

        [lease] = resource["leases"]
        assert lease["mode"] == "connection"
        assert lease["standing"] == "connected"
        assert lease["alert"] is False
        assert [block.type for block in kept.content] == ["text"]
        assert kept.content[0].text == "In [2]:\n5\n"
        await wait_for_resource_count(origin, 0, timeout=5)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(
        reserve_port(),
        workspace,
        lease_mode="connection",
        connection_grace_seconds=0.3,
    ) as origin:
        asyncio.run(scenario(origin))


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="external Streamable HTTP subprocess harness is Linux-only",
)
@pytest.mark.timeout(60)
def test_a_session_without_a_standalone_stream_ends_its_results_with_an_alert(
    tmp_path: Path,
) -> None:
    """A hand-written client that never opens GET stands in for a host whose transport changed."""
    headers = {"Accept": "application/json, text/event-stream"}

    def rpc(http: httpx.Client, session: str | None, method: str, params: dict | None = None, request_id: int | None = None) -> httpx.Response:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if request_id is not None:
            message["id"] = request_id
        session_header = {"mcp-session-id": session} if session else {}
        return http.post("/mcp", json=message, headers={**headers, **session_header})

    def scenario(origin: str) -> None:
        with httpx.Client(base_url=origin, timeout=5) as http:
            initialized = rpc(http, None, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "streamless", "version": "0"}}, 1)
            session = initialized.headers["mcp-session-id"]
            rpc(http, session, "notifications/initialized")
            awaiting = rpc(http, session, "tools/call", {"name": "status", "arguments": {}}, 2).text
            time.sleep(0.5)
            alerted = rpc(http, session, "tools/call", {"name": "status", "arguments": {}}, 3).text
            [resource] = http.get("/api/resources").json()["resources"]

        assert "Lease alert" not in awaiting
        assert "Lease alert (unbound): this MCP session never opened the standalone GET stream" in alerted
        assert "expires after 30 idle seconds like an activity lease" in alerted
        [lease] = resource["leases"]
        assert lease["standing"] == "unbound"
        assert lease["alert"] is True

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(
        reserve_port(),
        workspace,
        lease_mode="connection",
        connection_grace_seconds=0.3,
        # The degraded lease must outlive the observation; with the default
        # 0.2 s activity window it would be reclaimed before the second call.
        private_activity_timeout_seconds=30,
    ) as origin:
        scenario(origin)

"""Demonstrate the recommended loommux lease-aware client lifecycle."""

from __future__ import annotations

import asyncio
import os

import httpx
from fastmcp.client.client import CallToolResult

from loommux.client import LeaseAwareClient

SERVER_URL = os.getenv("LOOMMUX_SERVER_URL", "http://127.0.0.1:8801/mcp")
OPERATOR = os.getenv("LOOMMUX_OPERATOR", "lease-aware-demo")
RESOURCE_NAME = os.getenv("LOOMMUX_RESOURCE_NAME", "shared-heartbeat-demo")
OBSERVE_SECONDS = float(os.getenv("LOOMMUX_OBSERVE_SECONDS", "8"))


def first_text(result: CallToolResult) -> str:
    """Return the first text block for the compact content-only demo output."""
    for block in result.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return "<no text content>"


async def wait_until_resource_is_reclaimed(
    control_url: str,
    resource_name: str,
    *,
    timeout_seconds: float = 15,
) -> None:
    """Observe post-context reclamation without treating it as client cleanup."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    async with httpx.AsyncClient(timeout=2) as http:
        while asyncio.get_running_loop().time() < deadline:
            response = await http.get(f"{control_url}/api/resources")
            response.raise_for_status()
            resources = response.json()["resources"]
            if not any(item["name"] == resource_name for item in resources):
                print("server reclaimed the resource after heartbeats stopped")
                return
            await asyncio.sleep(0.25)
    print("resource was not reclaimed within the observation window")


async def main() -> None:
    """Keep one shared namespace alive, then demonstrate normal lease expiry."""
    client = LeaseAwareClient(
        SERVER_URL,
        OPERATOR,
        resource_name=RESOURCE_NAME,
    )
    async with client:
        assert client.policy is not None
        print(
            "policy:",
            client.policy.mode,
            f"generation={client.policy.generation}",
            f"interval={client.policy.heartbeat_interval_seconds}s",
            f"timeout={client.policy.heartbeat_timeout_seconds}s",
        )

        first = await client.call_tool(
            "run_cell",
            {
                "freeform": (
                    "heartbeat_demo_value = 41\n"
                    "print('created:', heartbeat_demo_value)"
                )
            },
        )
        print(first_text(first), end="")

        print(f"observing silently for {OBSERVE_SECONDS:g}s ...")
        await asyncio.sleep(OBSERVE_SECONDS)

        second = await client.call_tool(
            "run_cell",
            {
                "freeform": (
                    "heartbeat_demo_value += 1\n"
                    "print('preserved:', heartbeat_demo_value)"
                )
            },
        )
        print(first_text(second), end="")
        print(f"successful heartbeats: {client.heartbeat_count}")
        if client.last_heartbeat_error:
            print(f"last heartbeat error: {client.last_heartbeat_error}")

    await wait_until_resource_is_reclaimed(
        client.control_url,
        RESOURCE_NAME,
    )


if __name__ == "__main__":
    asyncio.run(main())

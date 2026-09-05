"""Show the minimum manual cooperation required from a FastMCP client."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from math import isfinite
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StreamableHttpTransport

from loommux.resource import (
    LEASE_POLICY_GENERATION_HEADER,
    OPERATOR_HEADER,
    RESOURCE_HEADER,
)

SERVER_URL = os.getenv("LOOMMUX_SERVER_URL", "http://127.0.0.1:8801/mcp")
OPERATOR = os.getenv("LOOMMUX_OPERATOR", "manual-fastmcp-demo")
RESOURCE_NAME = os.getenv("LOOMMUX_RESOURCE_NAME", "manual-heartbeat-demo")
OBSERVE_SECONDS = float(os.getenv("LOOMMUX_OBSERVE_SECONDS", "8"))


@dataclass(frozen=True)
class Policy:
    """The server policy snapshot used by this one MCP connection."""

    mode: str
    generation: int
    heartbeat_interval_seconds: float
    heartbeat_timeout_seconds: float


async def fetch_policy(server_url: str) -> Policy:
    """Discover policy before MCP initialization to close the setup race."""
    parts = urlsplit(server_url)
    control_url = urlunsplit((parts.scheme, parts.netloc, "", "", "")).rstrip("/")
    async with httpx.AsyncClient(timeout=5) as http:
        response = await http.get(f"{control_url}/api/lease-policy")
        response.raise_for_status()
    payload: dict[str, Any] = response.json()["policy"]
    policy = Policy(
        mode=str(payload["mode"]),
        generation=int(payload["generation"]),
        heartbeat_interval_seconds=float(
            payload["heartbeat_interval_seconds"]
        ),
        heartbeat_timeout_seconds=float(payload["heartbeat_timeout_seconds"]),
    )
    if policy.mode not in {"activity", "heartbeat"}:
        raise ValueError(f"unknown lease mode: {policy.mode}")
    if (
        not isfinite(policy.heartbeat_interval_seconds)
        or policy.heartbeat_interval_seconds <= 0
        or not isfinite(policy.heartbeat_timeout_seconds)
        or policy.heartbeat_timeout_seconds <= policy.heartbeat_interval_seconds
    ):
        raise ValueError("server returned an invalid heartbeat window")
    return policy


def text_of(result: CallToolResult) -> str:
    """Keep the manual example focused on the text projection."""
    for block in result.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return "<no text content>"


async def main() -> None:
    """Use FastMCP directly while explicitly owning the heartbeat task."""
    policy = await fetch_policy(SERVER_URL)
    headers = {
        OPERATOR_HEADER: quote(OPERATOR, safe=""),
        RESOURCE_HEADER: quote(RESOURCE_NAME, safe=""),
        LEASE_POLICY_GENERATION_HEADER: str(policy.generation),
    }
    transport = StreamableHttpTransport(SERVER_URL, headers=headers)

    async with Client(transport) as client:
        created = await client.call_tool(
            "run_cell",
            {"freeform": "manual_demo_value = 100\nprint(manual_demo_value)"},
        )
        print(text_of(created), end="")

        if policy.mode != "heartbeat":
            print(f"server mode is {policy.mode}; no background ping required")
            return

        deadline = asyncio.get_running_loop().time() + OBSERVE_SECONDS
        heartbeat_count = 0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(policy.heartbeat_interval_seconds)
            if await client.ping():
                heartbeat_count += 1
                print(f"ping {heartbeat_count}: ok")
            else:
                print(f"ping {heartbeat_count + 1}: non-standard response")

        preserved = await client.call_tool(
            "run_cell",
            {"freeform": "manual_demo_value += 1\nprint(manual_demo_value)"},
        )
        print(text_of(preserved), end="")
        print(f"manual heartbeats: {heartbeat_count}")


if __name__ == "__main__":
    asyncio.run(main())

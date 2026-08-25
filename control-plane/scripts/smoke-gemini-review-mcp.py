from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run() -> None:
    script = Path(__file__).resolve().with_name("run-gemini-review-mcp.sh")
    server = StdioServerParameters(command="/bin/zsh", args=[str(script)])
    async with Client(stdio_client(server)) as client:
        result = await client.list_tools()
        names = sorted(tool.name for tool in result.tools)
        if names != ["gemini_review"]:
            raise RuntimeError(f"unexpected MCP tools: {names}")
        print(json.dumps({
            "status": "GEMINI_REVIEW_MCP_STDIO_PASS",
            "tools": names,
            "protocol_version": client.protocol_version,
        }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())

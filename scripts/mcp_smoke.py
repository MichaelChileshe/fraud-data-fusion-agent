"""Talk to the MCP server over the real protocol, the way Claude Desktop or the agent does.

Starts `python -m fusion.mcp_server` as a child process (stdio), lists its tools,
and calls a few of them against the running API.

Run:  python scripts/mcp_smoke.py            (the API must be running on :8000)
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SHARED_PHONE = "phone:+27735963987"  # the controller's second phone (ground truth, seed 42)


async def call(session: ClientSession, tool: str, **args) -> dict:
    result = await session.call_tool(tool, args)
    return json.loads(result.content[0].text)


async def main() -> None:
    server = StdioServerParameters(command=sys.executable, args=["-m", "fusion.mcp_server"],
                                   env=dict(os.environ))
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("Tools the server offers:")
            for t in tools.tools:
                print(f"  {t.name:<20} {list(t.inputSchema['properties'])}")

            print("\nsearch_entities('Sipho Dlamini'):")
            for hit in (await call(session, "search_entities", query="Sipho Dlamini", limit=5))["hits"]:
                print(f"  {hit['node_id']:<18} score {hit['score']:<6} evidence {hit['evidence_ids']}")

            print(f"\nget_connections('{SHARED_PHONE}'):")
            net = await call(session, "get_connections", node_id=SHARED_PHONE)
            for e in net.get("edges", []):
                print(f"  {e['src']:<18} {e['kind']:<17} evidence {e['evidence_ids']}")

            print("\nget_evidence('NOPE'):")
            print(" ", await call(session, "get_evidence", evidence_id="NOPE"))


if __name__ == "__main__":
    asyncio.run(main())

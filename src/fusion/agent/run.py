"""Ask the investigation agent a question from the command line.

    python -m fusion.agent.run "Which accounts share a device with A005373?"

The agent reaches the data only through the MCP server, which this script
starts as a child process (stdio). Each run is saved to results/agent-runs/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fusion import telemetry
from fusion.config import settings


def make_llm():
    from langchain_ollama import ChatOllama

    # temperature 0: the same question should get the same investigation.
    # num_ctx 16k: tool results must fit in the model's context window.
    return ChatOllama(model=settings.chat_model, base_url=settings.ollama_url,
                      temperature=0, num_ctx=16384)


@asynccontextmanager
async def mcp_tools():
    """Start the MCP server over stdio and yield its tools as LangChain tools."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools

    client = MultiServerMCPClient({
        "fusion": {"command": sys.executable, "args": ["-m", "fusion.mcp_server"],
                   "transport": "stdio", "env": dict(os.environ)},
    })
    async with client.session("fusion") as session:
        yield await load_mcp_tools(session)


async def ask(questions: list[str], llm=None) -> list[dict]:
    from fusion.agent.graph import build_graph, investigate

    llm = llm or make_llm()
    results = []
    async with mcp_tools() as tools:
        graph = build_graph(llm, tools)
        for q in questions:
            with telemetry.span("agent.investigate", question=q[:200]):
                results.append(await investigate(graph, q))
    return results


def save(result: dict) -> Path:
    out = Path(settings.results_dir) / "agent-runs"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    args = ap.parse_args()
    telemetry.setup("fusion-agent")
    (result,) = asyncio.run(ask([args.question]))
    telemetry.flush()
    print("\n" + result["answer"] + "\n")
    print(f"Tool calls ({len(result['tool_calls'])}): " +
          ", ".join(c["name"] for c in result["tool_calls"]))
    print(f"Evidence cited: {len(result['cited_evidence'])} | verified: {len(result['verified_evidence'])}"
          f" | NOT verified: {result['unverified_evidence'] or 'none'}")
    print(f"Tokens in/out: {result['input_tokens']:,}/{result['output_tokens']:,} | "
          f"{result['latency_s']} s | {result['status']}")
    print(f"Saved: {save(result)}")


if __name__ == "__main__":
    main()

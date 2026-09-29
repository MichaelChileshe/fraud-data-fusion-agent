"""MCP server: the fusion API as five tools an AI agent (or Claude Desktop) can call.

Why tools instead of pasting data into the prompt: the data is millions of
tokens; a tool call returns only the few hundred tokens that answer the
question, with evidence ids attached. The model decides what to look up.

Each tool is a thin, documented wrapper over one API endpoint. The
docstrings matter: they are what the model reads to choose a tool.

Run (stdio, for Claude Desktop / MCP Inspector / the agent):
    python -m fusion.mcp_server
Run (HTTP, for remote clients):
    python -m fusion.mcp_server --http --port 8765
"""

from __future__ import annotations

import argparse
import json

import httpx
from mcp.server.fastmcp import FastMCP

from fusion import telemetry
from fusion.config import settings

mcp = FastMCP("kgotla-fraud-fusion")
_client: httpx.Client | None = None


def api() -> httpx.Client:
    global _client
    if _client is None:
        telemetry.instrument_httpx()
        _client = httpx.Client(base_url=settings.api_url, timeout=30)
    return _client


def _call(path: str, **params) -> str:
    """GET an endpoint and return compact JSON text (errors are returned, not raised)."""
    params = {k: v for k, v in params.items() if v is not None}
    with telemetry.span("mcp.tool", path=path):
        r = api().get(path, params=params)
    if r.status_code >= 400:
        is_json = r.headers.get("content-type", "").startswith("application/json")
        detail = r.json().get("detail", r.text) if is_json else r.text
        return json.dumps({"error": r.status_code, "detail": detail})
    return json.dumps(r.json(), separators=(",", ":"), default=str)


@mcp.tool()
def search_entities(query: str, limit: int = 10) -> str:
    """Find people, accounts, phones, devices or sanctions entries.

    `query` can be a name ("Sipho Dlamini"), an account id (A000123), a person
    id (P-000042), a phone number in any format, a device id (D-1a2b3c4d) or a
    sanctions entry id (S-083). Returns matches with node ids like
    'account:A000123' to pass to get_connections, plus evidence ids.
    """
    return _call("/entities/search", q=query, limit=limit)


@mcp.tool()
def get_connections(node_id: str, depth: int = 1, kinds: list[str] | None = None) -> str:
    """Show what a node is linked to: owners, accounts, phones, devices, payments, sanctions matches.

    `node_id` is a typed id such as 'account:A000123' or 'person:P-000042'.
    `depth` 1 = direct links; 2 = links of links (e.g. other accounts that
    use the same device). `kinds` limits which links to follow, any of:
    OWNS, REGISTERED_PHONE, LOGGED_IN_FROM, SENT_TO, SANCTIONS_MATCH.
    Tip: to find accounts sharing a device or phone with an account, use
    depth=2 with kinds=["LOGGED_IN_FROM"] or ["REGISTERED_PHONE"].
    Every link lists the evidence ids that prove it.
    """
    return _call(f"/entities/{node_id}/network", depth=depth, kinds=kinds)


@mcp.tool()
def transaction_summary(account_id: str, days: int = 365) -> str:
    """Money in and out of one account (A000123) over the last `days` days.

    Returns inbound/outbound counts and totals in rand, distinct senders and
    receivers, the top 5 counterparties, and pass_through_ratio
    (outbound / inbound). A ratio near 1.0 with many distinct senders is the
    classic pattern of an account that receives and forwards other people's money.
    """
    return _call(f"/accounts/{account_id}/summary", days=days)


@mcp.tool()
def search_case_notes(query: str, k: int = 5, account_id: str | None = None) -> str:
    """Search fraud analysts' free-text case notes by meaning, not keywords.

    Describe what you are looking for in plain words (e.g. "customer let
    someone else use the account"). Optionally restrict to one account.
    Returns the closest notes with their note ids (N-00042) and distance
    (lower = closer).
    """
    return _call("/case-notes/search", q=query, k=k, account_id=account_id)


@mcp.tool()
def get_evidence(evidence_id: str) -> str:
    """Fetch the original source record behind an evidence id.

    Ids look like KYC-000123 (account opening), T0012345 (transaction),
    L0004567 (login), S-083 (sanctions entry) or N-00042 (case note).
    Use it to confirm a fact before stating it.
    """
    return _call(f"/evidence/{evidence_id}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    telemetry.setup("fusion-mcp")
    if args.http:
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()

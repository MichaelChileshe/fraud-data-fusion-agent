"""The MCP layer: exactly five documented tools, compact JSON out, errors returned as data."""

import json

import httpx
import pytest

from fusion import mcp_server


def test_mcp_exposes_five_documented_tools():
    tools = {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}
    assert set(tools) == {"search_entities", "get_connections", "transaction_summary",
                          "search_case_notes", "get_evidence"}
    assert all(len(t.description) > 80 for t in tools.values())


@pytest.fixture()
def fake_api(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/evidence/S-083":
            return httpx.Response(200, json={"evidence_id": "S-083", "payload": {}})
        return httpx.Response(404, json={"detail": "no evidence with id X"})
    monkeypatch.setattr(mcp_server, "_client", httpx.Client(base_url="http://api",
                                                             transport=httpx.MockTransport(handler)))


def test_tools_return_compact_json_and_errors_instead_of_raising(fake_api):
    assert json.loads(mcp_server.get_evidence("S-083"))["evidence_id"] == "S-083"
    assert json.loads(mcp_server.get_evidence("X")) == {"error": 404, "detail": "no evidence with id X"}

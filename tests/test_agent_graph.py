"""The agent graph, with a scripted model and local tools: no LLM, no network."""

import asyncio

from fakes import ScriptedChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from fusion.agent import graph as g


@tool
def get_connections(node_id: str, depth: int = 1) -> str:
    """Links of a node."""
    return '{"edges":[{"src":"account:A005386","dst":"device:D-75f56418","evidence_ids":["L0000001"]}]}'


@tool
def transaction_summary(account_id: str) -> str:
    """Money in and out."""
    return '{"pass_through_ratio":0.9,"evidence_ids":["T0000145"]}'


def call(name, args, i):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


def run(replies, question="Which accounts share a device with A005386?"):
    llm = ScriptedChatModel(replies=replies)
    compiled = g.build_graph(llm, [get_connections, transaction_summary])
    return asyncio.run(g.investigate(compiled, question)), llm


def test_cited_ids_are_checked_against_what_the_tools_returned():
    result, _ = run([
        call("get_connections", {"node_id": "account:A005386", "depth": 2}, 1),
        call("transaction_summary", {"account_id": "A005386"}, 2),
        AIMessage("Finding: shares a device [L0000001], forwards money [T0000145], and [T9999999].\n"
                  "Accounts: A005386, A005382"),
    ])
    assert result["verified_evidence"] == ["L0000001", "T0000145"]
    assert result["unverified_evidence"] == ["T9999999"]
    assert result["accounts_mentioned"] == ["A005382", "A005386"]
    assert [c["name"] for c in result["tool_calls"]] == ["get_connections", "transaction_summary"]
    assert result["status"] == "PENDING ANALYST REVIEW"


def test_an_answer_without_tools_has_nothing_verified():
    result, _ = run([AIMessage("A005386 is definitely a mule [T0000145].")])
    assert result["tool_calls"] == [] and result["unverified_evidence"] == ["T0000145"]


def test_the_step_limit_forces_a_final_answer():
    loop = [call("get_connections", {"node_id": "account:A005386"}, i) for i in range(g.MAX_TOOL_CALLS + 1)]
    result, llm = run(loop + [AIMessage("Finding: out of steps, partial answer [L0000001].")])
    assert len(result["tool_calls"]) == g.MAX_TOOL_CALLS + 1  # the last request was never executed
    assert "partial answer" in result["answer"]
    assert result["verified_evidence"] == ["L0000001"]

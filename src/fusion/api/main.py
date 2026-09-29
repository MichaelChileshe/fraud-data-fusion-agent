"""The fusion API: five read-only endpoints over the fused data.

This is the boundary between "data" and "AI" (ADR-0005): the agent never
touches a database. It calls tools, the tools call these endpoints, and
every response is typed and carries evidence ids.

Run:  uvicorn fusion.api.main:app --port 8000        (docs at http://localhost:8000/docs)
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from fusion import telemetry
from fusion.api.schemas import (
    Evidence, Health, LinkKind, NetworkResponse, NotesResponse, SearchResponse, TxnSummary,
)
from fusion.config import settings

NODE_ID = r"^(person|account|phone|device|sanction):\S+$"


def build_repository():
    from psycopg_pool import ConnectionPool

    from fusion.api.repository import PostgresTxnStore, Repository
    from fusion.notes import search as note_search

    pool = ConnectionPool(settings.pg_dsn, min_size=1, max_size=8, open=True,
                          kwargs={"autocommit": True})
    if settings.txn_store == "clickhouse":
        from fusion.warehouse import ClickHouseTxnStore
        store = ClickHouseTxnStore()
    else:
        store = PostgresTxnStore(pool)
    return Repository(pool, store, note_search)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "repo"):  # tests set their own repository first
        app.state.repo = build_repository()
    yield
    pool = getattr(app.state.repo, "pool", None)
    if pool is not None:
        pool.close()


telemetry.setup("fusion-api")
app = FastAPI(
    title="Kgotla fraud fusion API",
    version="1.0.0",
    description="Read-only access to fused KYC, transaction, login, sanctions and case-note data. "
                "Every response carries the evidence ids of the source records behind it.",
    lifespan=lifespan,
)
telemetry.instrument_fastapi(app)


def repo(request: Request):
    return request.app.state.repo


@app.get("/health", response_model=Health, tags=["ops"])
def health(r=Depends(repo)):
    ok = r.ping()
    return Health(status="ok" if ok else "degraded", postgres=ok, txn_store=r.txn_store.name)


@app.get("/entities/search", response_model=SearchResponse, tags=["entities"])
def search_entities(q: str = Query(min_length=2, max_length=100,
                                   description="Name, account id, person id, phone, device id or sanctions id"),
                    limit: int = Query(10, ge=1, le=50), r=Depends(repo)):
    return SearchResponse(query=q, hits=r.search(q, limit))


@app.get("/entities/{node_id}/network", response_model=NetworkResponse, tags=["entities"])
def network(node_id: str, depth: int = Query(1, ge=1, le=3),
            kinds: list[LinkKind] | None = Query(None, description="Only follow these link kinds"),
            r=Depends(repo)):
    if not re.match(NODE_ID, node_id):
        raise HTTPException(422, "node_id must look like 'account:A000123' or 'person:P-000042'")
    result = r.network(node_id, depth, kinds)
    if len(result["nodes"]) <= 1 and not result["edges"]:
        raise HTTPException(404, f"{node_id} has no links")
    return result


@app.get("/accounts/{account_id}/summary", response_model=TxnSummary, tags=["transactions"])
def transaction_summary(account_id: str, days: int = Query(365, ge=1, le=3650), r=Depends(repo)):
    if not re.match(r"^A\d{6}$", account_id):
        raise HTTPException(422, "account_id must look like A000123")
    return r.txn_summary(account_id, days)


@app.get("/case-notes/search", response_model=NotesResponse, tags=["case notes"])
def search_notes(q: str = Query(min_length=3, max_length=500), k: int = Query(5, ge=1, le=20),
                 account_id: str | None = Query(None, pattern=r"^A\d{6}$"), r=Depends(repo)):
    return NotesResponse(query=q, hits=r.notes(q, k, account_id))


@app.get("/evidence/{evidence_id}", response_model=Evidence, tags=["evidence"])
def get_evidence(evidence_id: str, r=Depends(repo)):
    row = r.evidence(evidence_id)
    if row is None:
        raise HTTPException(404, f"no evidence with id {evidence_id}")
    return row

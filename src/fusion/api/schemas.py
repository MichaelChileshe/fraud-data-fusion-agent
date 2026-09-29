"""Response models: the API's contract. Every answer carries the evidence behind it."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LinkKind = Literal["OWNS", "REGISTERED_PHONE", "LOGGED_IN_FROM", "SENT_TO", "SANCTIONS_MATCH"]


class EntityHit(BaseModel):
    node_id: str = Field(description="Typed id, e.g. 'account:A000123' or 'person:P-000042'")
    kind: Literal["person", "account", "phone", "device", "sanction"]
    label: str
    detail: str = ""
    score: float = Field(description="1.0 for an exact id match, otherwise name similarity 0-1")
    evidence_ids: list[str] = []


class SearchResponse(BaseModel):
    query: str
    hits: list[EntityHit]


class Node(BaseModel):
    node_id: str
    kind: str
    label: str


class Edge(BaseModel):
    src: str
    dst: str
    kind: LinkKind
    weight: int = Field(description="How many events support this link")
    total_zar: float | None = None
    score: float | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    evidence_ids: list[str]


class NetworkResponse(BaseModel):
    center: str
    depth: int
    nodes: list[Node]
    edges: list[Edge]
    truncated: bool = Field(description="True if limits cut the network short")


class Counterparty(BaseModel):
    account_id: str
    direction: Literal["in", "out"]
    count: int
    total_zar: float


class TxnSummary(BaseModel):
    account_id: str
    days: int
    store: Literal["clickhouse", "postgres"]
    inbound_count: int
    inbound_total_zar: float
    outbound_count: int
    outbound_total_zar: float
    pass_through_ratio: float | None = Field(
        description="outbound total / inbound total; near 1.0 means money passes straight through")
    distinct_senders: int
    distinct_receivers: int
    first_seen: datetime | None
    last_seen: datetime | None
    top_counterparties: list[Counterparty]
    evidence_ids: list[str]


class NoteHit(BaseModel):
    note_id: str
    account_id: str
    created_at: datetime
    text: str
    distance: float = Field(description="Cosine distance to the query; lower is closer")


class NotesResponse(BaseModel):
    query: str
    hits: list[NoteHit]


class Evidence(BaseModel):
    evidence_id: str
    source: str
    payload: dict
    received_at: datetime


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    postgres: bool
    txn_store: str

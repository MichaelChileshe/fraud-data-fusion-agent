"""The API contract, tested without a database (the repository is a fake)."""

import pytest
from fakes import FakeRepository
from fastapi.testclient import TestClient

from fusion.api.main import app


@pytest.fixture()
def client():
    app.state.repo = FakeRepository()
    with TestClient(app) as c:
        yield c
    del app.state.repo


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "postgres": True, "txn_store": "postgres"}


def test_search_returns_typed_hits_with_evidence(client):
    body = client.get("/entities/search", params={"q": "Sipho"}).json()
    assert body["hits"][0]["node_id"] == "account:A005382"
    assert body["hits"][0]["evidence_ids"] == ["KYC-005382"]


def test_search_validates_the_query(client):
    assert client.get("/entities/search", params={"q": "x"}).status_code == 422


def test_network_rejects_untyped_ids_and_404s_unknown_nodes(client):
    assert client.get("/entities/A005382/network").status_code == 422
    assert client.get("/entities/account:A000009/network").status_code == 404
    body = client.get("/entities/account:A005382/network", params={"depth": 2}).json()
    assert body["edges"][0]["kind"] == "LOGGED_IN_FROM" and body["edges"][0]["evidence_ids"]


def test_network_only_accepts_known_link_kinds(client):
    r = client.get("/entities/account:A005382/network", params={"kinds": ["FRIENDS_WITH"]})
    assert r.status_code == 422


def test_summary(client):
    body = client.get("/accounts/A005382/summary").json()
    assert body["pass_through_ratio"] == 0.9 and body["evidence_ids"] == ["T0000001"]
    assert client.get("/accounts/12345/summary").status_code == 422


def test_case_notes(client):
    body = client.get("/case-notes/search", params={"q": "someone else used the account"}).json()
    assert body["hits"][0]["note_id"] == "N-00001"


def test_evidence(client):
    assert client.get("/evidence/S-083").json()["payload"]["full_name"] == "Sipho Mandla Dlamini"
    assert client.get("/evidence/NOPE").status_code == 404


def test_openapi_documents_every_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/entities/search", "/entities/{node_id}/network",
            "/accounts/{account_id}/summary", "/case-notes/search", "/evidence/{evidence_id}"} <= set(paths)

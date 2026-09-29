"""Test doubles: an in-memory repository that answers like the real one."""

from __future__ import annotations

from datetime import datetime, timezone

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


class _Store:
    name = "postgres"


class FakeRepository:
    """Answers like the real Repository, from a few hard-coded rows."""

    txn_store = _Store()

    def ping(self) -> bool:
        return True

    def search(self, q: str, limit: int = 10) -> list[dict]:
        if "sipho" not in q.lower() and q != "A005382":
            return []
        return [{"node_id": "account:A005382", "kind": "account", "label": "Sipho M. Dlamini",
                 "detail": "owner person:P-000001", "score": 1.0, "evidence_ids": ["KYC-005382"]}]

    def network(self, center: str, depth: int = 1, kinds=None) -> dict:
        if center != "account:A005382":
            return {"center": center, "depth": depth, "nodes": [], "edges": [], "truncated": False}
        return {
            "center": center, "depth": depth, "truncated": False,
            "nodes": [{"node_id": "account:A005382", "kind": "account", "label": "Sipho M. Dlamini"},
                      {"node_id": "device:D-0000beef", "kind": "device", "label": "D-0000beef"}],
            "edges": [{"src": "account:A005382", "dst": "device:D-0000beef", "kind": "LOGGED_IN_FROM",
                       "weight": 3, "evidence_ids": ["L0000001"], "first_seen": NOW, "last_seen": NOW}],
        }

    def txn_summary(self, account_id: str, days: int) -> dict:
        return {"account_id": account_id, "days": days, "store": "postgres",
                "inbound_count": 2, "inbound_total_zar": 1000.0, "outbound_count": 1,
                "outbound_total_zar": 900.0, "pass_through_ratio": 0.9, "distinct_senders": 2,
                "distinct_receivers": 1, "first_seen": NOW, "last_seen": NOW,
                "top_counterparties": [{"account_id": "A000001", "direction": "in", "count": 1,
                                        "total_zar": 600.0}],
                "evidence_ids": ["T0000001"]}

    def notes(self, query: str, k: int, account_id: str | None) -> list[dict]:
        return [{"note_id": "N-00001", "account_id": "A005382", "created_at": NOW,
                 "text": "Customer said a friend used the account.", "distance": 0.12}]

    def evidence(self, evidence_id: str) -> dict | None:
        if evidence_id != "S-083":
            return None
        return {"evidence_id": "S-083", "source": "sanctions", "received_at": NOW,
                "payload": {"entry_id": "S-083", "full_name": "Sipho Mandla Dlamini"}}

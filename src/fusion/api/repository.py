"""All the SQL behind the API, in one place.

`Repository` is the only thing the endpoints talk to. The tests swap it for
a fake, so the API contract is tested without a database, and a second set
of tests runs this real class against PostgreSQL.
"""

from __future__ import annotations

import re
from collections import deque

from psycopg_pool import ConnectionPool

from fusion.normalise import normalise_name, normalise_phone

ID_PATTERNS = {
    "account": re.compile(r"^A\d{6}$"),
    "person": re.compile(r"^P-\d{6}$"),
    "device": re.compile(r"^D-[0-9a-f]{8}$"),
    "sanction": re.compile(r"^S-\d{3}$"),
}

# Links that say who someone IS always come before links that say what they DID,
# so a busy account's ownership and phone survive when the per-node limit cuts in.
IDENTITY_KINDS = ["OWNS", "REGISTERED_PHONE", "SANCTIONS_MATCH"]


class PostgresTxnStore:
    """Per-account transaction summary, computed by PostgreSQL (for comparison with ClickHouse)."""

    name = "postgres"

    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def summary(self, account_id: str, days: int, table: str = "transactions") -> dict:
        from fusion.warehouse import _shape

        p = {"a": account_id, "d": days}
        where = (f"FROM {table} WHERE (to_account = %(a)s OR from_account = %(a)s) "
                 "AND ts >= now() - make_interval(days => %(d)s)")
        with self.pool.connection() as conn:
            row = conn.execute(f"""
                SELECT count(*) FILTER (WHERE to_account = %(a)s),
                       sum(amount_zar) FILTER (WHERE to_account = %(a)s),
                       count(*) FILTER (WHERE from_account = %(a)s),
                       sum(amount_zar) FILTER (WHERE from_account = %(a)s),
                       count(DISTINCT from_account) FILTER (WHERE to_account = %(a)s),
                       count(DISTINCT to_account) FILTER (WHERE from_account = %(a)s),
                       min(ts), max(ts)
                {where}""", p).fetchone()
            top = conn.execute(f"""
                SELECT CASE WHEN to_account = %(a)s THEN from_account ELSE to_account END,
                       CASE WHEN to_account = %(a)s THEN 'in' ELSE 'out' END,
                       count(*), sum(amount_zar)
                {where}
                GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 5""", p).fetchall()
            evidence = conn.execute(f"SELECT txn_id {where} ORDER BY amount_zar DESC LIMIT 5", p).fetchall()
        return _shape(account_id, days, row, top, [e[0] for e in evidence], self.name)


class Repository:
    def __init__(self, pool: ConnectionPool, txn_store, note_search=None):
        self.pool = pool
        self.txn_store = txn_store
        self.note_search = note_search  # callable(conn, query, k, account_id) -> list[dict]

    # -------------------------------------------------------------- health
    def ping(self) -> bool:
        try:
            with self.pool.connection() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    # -------------------------------------------------------------- search
    def search(self, q: str, limit: int = 10) -> list[dict]:
        q = q.strip()
        with self.pool.connection() as conn:
            if ID_PATTERNS["account"].match(q):
                return self._accounts(conn, "a.account_id = %s", (q,), 1.0)
            if ID_PATTERNS["person"].match(q):
                return self._accounts(conn, "a.person_id = %s", (q,), 1.0)
            if ID_PATTERNS["device"].match(q) or ID_PATTERNS["sanction"].match(q):
                kind = "device" if q.startswith("D-") else "sanction"
                node = f"{kind}:{q}"
                n = conn.execute("SELECT count(*) FROM links WHERE dst = %s", (node,)).fetchone()[0]
                if not n:
                    return []
                return [{"node_id": node, "kind": kind, "label": q, "score": 1.0,
                         "detail": f"linked to {n} other node(s)", "evidence_ids": []}]
            phone = normalise_phone(q)
            if phone:
                hits = self._accounts(conn, "a.phone_e164 = %s", (phone,), 1.0)
                return [{"node_id": f"phone:{phone}", "kind": "phone", "label": phone, "score": 1.0,
                         "detail": f"registered on {len(hits)} account(s)", "evidence_ids": []}] + hits
            name = normalise_name(q)
            hits = self._accounts(conn, "similarity(a.name_norm, %s) >= 0.3", (name,), None,
                                  order_by_sim=name, limit=limit)
            hits += [
                {"node_id": f"sanction:{e}", "kind": "sanction", "label": full, "score": round(float(s), 3),
                 "detail": f"{lst} entry, date of birth {dob or 'not listed'}", "evidence_ids": [e]}
                for e, full, lst, dob, s in conn.execute(
                    """SELECT entry_id, full_name, list_name, date_of_birth, similarity(name_norm, %s) s
                       FROM sanctions WHERE similarity(name_norm, %s) >= 0.5 ORDER BY s DESC LIMIT 3""",
                    (name, name)).fetchall()
            ]
            return sorted(hits, key=lambda h: -h["score"])[:limit]

    def _accounts(self, conn, where: str, params: tuple, score: float | None,
                  order_by_sim: str | None = None, limit: int = 25) -> list[dict]:
        sim = "similarity(a.name_norm, %s)" if order_by_sim else "1.0"
        sql = f"""SELECT a.account_id, a.full_name, a.person_id, a.phone_e164, a.city, a.opened_at,
                         a.kyc_evidence_id, a.matched_rule, {sim} AS s
                  FROM accounts a WHERE {where} ORDER BY s DESC, a.account_id LIMIT %s"""
        args = ((order_by_sim,) if order_by_sim else ()) + params + (limit,)
        rows = conn.execute(sql, args).fetchall()
        return [{
            "node_id": f"account:{acct}", "kind": "account", "label": name,
            "detail": f"owner person:{person} | phone {phone} | {city} | opened {opened:%Y-%m-%d}"
                      f" | resolved by {rule}",
            "score": score if score is not None else round(float(s), 3),
            "evidence_ids": [kyc],
        } for acct, name, person, phone, city, opened, kyc, rule, s in rows]

    # ------------------------------------------------------------- network
    def network(self, center: str, depth: int = 1, kinds: list[str] | None = None,
                per_node: int = 25, max_nodes: int = 150) -> dict:
        """Breadth-first walk over links in both directions, strongest links first."""
        seen = {center}
        edges: dict[tuple, dict] = {}
        frontier = deque([(center, 0)])
        truncated = False
        with self.pool.connection() as conn:
            while frontier:
                node, d = frontier.popleft()
                if d >= depth:
                    continue
                rows = conn.execute(
                    """SELECT src, dst, kind, weight, total_zar, score, first_seen, last_seen, evidence_ids
                       FROM links WHERE (src = %s OR dst = %s)
                         AND (%s::text[] IS NULL OR kind = ANY(%s::text[]))
                       ORDER BY kind = ANY(%s) DESC, weight DESC, total_zar DESC NULLS LAST LIMIT %s""",
                    (node, node, kinds, kinds, IDENTITY_KINDS, per_node + 1)).fetchall()
                if len(rows) > per_node:
                    truncated, rows = True, rows[:per_node]
                for src, dst, kind, w, tot, sc, fs, ls, ev in rows:
                    edges[(src, dst, kind)] = {
                        "src": src, "dst": dst, "kind": kind, "weight": w,
                        "total_zar": float(tot) if tot is not None else None,
                        "score": sc, "first_seen": fs, "last_seen": ls, "evidence_ids": ev[:5]}
                    other = dst if src == node else src
                    if other not in seen:
                        if len(seen) >= max_nodes:
                            truncated = True
                            continue
                        seen.add(other)
                        frontier.append((other, d + 1))
            nodes = self._labels(conn, seen)
        kept = {n["node_id"] for n in nodes}
        return {"center": center, "depth": depth, "nodes": nodes, "truncated": truncated,
                "edges": [e for e in edges.values() if e["src"] in kept and e["dst"] in kept]}

    def _labels(self, conn, node_ids: set[str]) -> list[dict]:
        accts = [n.split(":", 1)[1] for n in node_ids if n.startswith("account:")]
        persons = [n.split(":", 1)[1] for n in node_ids if n.startswith("person:")]
        names = dict(conn.execute("SELECT account_id, full_name FROM accounts WHERE account_id = ANY(%s)",
                                  (accts,)).fetchall())
        names.update(conn.execute("SELECT person_id, display_name FROM persons WHERE person_id = ANY(%s)",
                                  (persons,)).fetchall())
        out = []
        for n in sorted(node_ids):
            kind, ident = n.split(":", 1)
            out.append({"node_id": n, "kind": kind, "label": names.get(ident, ident)})
        return out

    # ------------------------------------------------------------ the rest
    def txn_summary(self, account_id: str, days: int) -> dict:
        return self.txn_store.summary(account_id, days)

    def notes(self, query: str, k: int, account_id: str | None) -> list[dict]:
        with self.pool.connection() as conn:
            return self.note_search(conn, query, k, account_id)

    def evidence(self, evidence_id: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT evidence_id, source, payload, received_at FROM evidence "
                               "WHERE evidence_id = %s", (evidence_id,)).fetchone()
        if not row:
            return None
        return dict(zip(("evidence_id", "source", "payload", "received_at"), row))

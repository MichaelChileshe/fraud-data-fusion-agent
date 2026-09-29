"""Entity resolution: fold records from five sources into people, accounts and links.

Rules (explainable on purpose: an analyst must be able to ask "why are
these the same person?" and get a one-line answer):

  Two account openings belong to the same PERSON when
    R1  the normalised phone AND the date of birth are equal, or
    R2  the e-mail address is equal, or
    R3  the names are similar (trigram similarity >= 0.6) AND the date of
        birth AND the city are equal.
  A shared phone or device on its own never merges people: it becomes a
  link, because shared phones and devices are exactly what a mule ring
  looks like and must stay visible, not be hidden inside one "person".

  A person is a possible SANCTIONS match when the name is similar
  (>= 0.6, checked against the entry's name and aliases) and the date of
  birth matches, or the name is near-identical (>= 0.8) and the entry has
  no date of birth. A match is a lead for an analyst, never a verdict.

Every write happens inside the caller's transaction, together with the
idempotency ledger entry, so an event is applied fully or not at all.
"""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from fusion.normalise import normalise_name, normalise_phone

NAME_MATCH = 0.6
SANCTIONS_MATCH = 0.6
SANCTIONS_MATCH_NO_DOB = 0.8


def _upsert_link(cur: psycopg.Cursor, src: str, dst: str, kind: str, evidence: str | list[str],
                 ts=None, amount: float | None = None, score: float | None = None) -> None:
    """Create a link, or strengthen it if it exists. `evidence` = the record id(s) behind it."""
    evidence_ids = [evidence] if isinstance(evidence, str) else evidence
    cur.execute(
        """
        INSERT INTO links (src, dst, kind, weight, total_zar, score, first_seen, last_seen, evidence_ids)
        VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s)
        ON CONFLICT (src, dst, kind) DO UPDATE SET
            weight = links.weight + 1,
            total_zar = CASE WHEN EXCLUDED.total_zar IS NULL THEN links.total_zar
                             ELSE COALESCE(links.total_zar, 0) + EXCLUDED.total_zar END,
            score = GREATEST(links.score, EXCLUDED.score),
            first_seen = LEAST(links.first_seen, EXCLUDED.first_seen),
            last_seen = GREATEST(links.last_seen, EXCLUDED.last_seen),
            evidence_ids = CASE WHEN cardinality(links.evidence_ids) < 20
                                THEN links.evidence_ids || EXCLUDED.evidence_ids
                                ELSE links.evidence_ids END
        """,
        (src, dst, kind, amount, score, ts, ts, evidence_ids),
    )


class Resolver:
    """Applies one validated record to PostgreSQL. `apply` is the only entry point."""

    def apply(self, conn: psycopg.Connection, source: str, record_id: str,
              model: BaseModel, raw: dict) -> bool:
        """Apply a record exactly once. Returns False if it was already applied."""
        with conn.transaction():
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO processed_events (event_key) VALUES (%s) ON CONFLICT DO NOTHING RETURNING 1",
                (f"{source}:{record_id}",),
            )
            if cur.fetchone() is None:
                return False  # seen before: a duplicate or a replay after a crash
            cur.execute(
                "INSERT INTO evidence (evidence_id, source, payload) VALUES (%s, %s, %s)",
                (record_id, source, Jsonb(raw)),
            )
            getattr(self, f"_{source}")(cur, record_id, model)
        return True

    # ------------------------------------------------------------------ KYC
    def _kyc(self, cur: psycopg.Cursor, rid: str, m) -> None:
        phone = normalise_phone(m.phone)  # the gate guarantees this is not None
        name = normalise_name(m.full_name)
        person_id, rule = self._find_person(cur, name, m.date_of_birth, phone, m.email, m.city)
        if person_id is None:
            cur.execute(
                """INSERT INTO persons (display_name, name_norm, date_of_birth, city, created_from)
                   VALUES (%s, %s, %s, %s, %s) RETURNING person_id""",
                (m.full_name.title(), name, m.date_of_birth, m.city, rid),
            )
            person_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO accounts (account_id, person_id, full_name, name_norm, phone_e164,
                                     email, city, opened_at, kyc_evidence_id, matched_rule)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (account_id) DO NOTHING""",
            (m.account_id, person_id, m.full_name, name, phone, m.email, m.city, m.opened_at, rid,
             rule or "NEW"),
        )
        acct = f"account:{m.account_id}"
        _upsert_link(cur, f"person:{person_id}", acct, "OWNS", rid, ts=m.opened_at)
        _upsert_link(cur, acct, f"phone:{phone}", "REGISTERED_PHONE", rid, ts=m.opened_at)
        # Screen this account holder's name (as written on this record) against sanctions.
        for entry_id, score in self._screen(cur, name, m.date_of_birth):
            _upsert_link(cur, f"person:{person_id}", f"sanction:{entry_id}", "SANCTIONS_MATCH",
                         [rid, entry_id], ts=m.opened_at, score=score)

    def _find_person(self, cur, name, dob, phone, email, city) -> tuple[str | None, str | None]:
        cur.execute(
            """SELECT a.person_id FROM accounts a JOIN persons p USING (person_id)
               WHERE a.phone_e164 = %s AND p.date_of_birth = %s LIMIT 1""",
            (phone, dob),
        )
        if (row := cur.fetchone()):
            return row[0], "R1"
        if email:
            cur.execute("SELECT person_id FROM accounts WHERE lower(email) = lower(%s) LIMIT 1", (email,))
            if (row := cur.fetchone()):
                return row[0], "R2"
        cur.execute(
            """SELECT person_id, similarity(name_norm, %s) AS s FROM persons
               WHERE date_of_birth = %s AND city = %s AND similarity(name_norm, %s) >= %s
               ORDER BY s DESC LIMIT 1""",
            (name, dob, city, name, NAME_MATCH),
        )
        if (row := cur.fetchone()):
            return row[0], "R3"
        return None, None

    def _screen(self, cur, name: str, dob) -> list[tuple[str, float]]:
        cur.execute(
            """SELECT entry_id, date_of_birth,
                      GREATEST(similarity(name_norm, %s),
                               COALESCE((SELECT max(similarity(a, %s)) FROM unnest(aliases_norm) a), 0)) AS s
               FROM sanctions""",
            (name, name),
        )
        hits = []
        for entry_id, entry_dob, s in cur.fetchall():
            if (entry_dob is not None and entry_dob == dob and s >= SANCTIONS_MATCH) or \
               (entry_dob is None and s >= SANCTIONS_MATCH_NO_DOB):
                hits.append((entry_id, round(float(s), 3)))
        return hits

    # ------------------------------------------------------------ sanctions
    def _sanctions(self, cur, rid: str, m) -> None:
        aliases = [normalise_name(a) for a in m.aliases]
        cur.execute(
            """INSERT INTO sanctions (entry_id, full_name, name_norm, aliases_norm, date_of_birth, list_name)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (rid, m.full_name, normalise_name(m.full_name), aliases, m.date_of_birth, m.list_name),
        )
        # Screen everyone already known against the new entry (lists change after onboarding).
        cur.execute("SELECT a.account_id, a.name_norm, p.person_id, p.date_of_birth, a.kyc_evidence_id "
                    "FROM accounts a JOIN persons p USING (person_id)")
        names = [normalise_name(m.full_name), *aliases]
        for _acct, acct_name, person_id, dob, kyc_id in cur.fetchall():
            s = max(_trigram(acct_name, n) for n in names)
            if (m.date_of_birth is not None and dob == m.date_of_birth and s >= SANCTIONS_MATCH) or \
               (m.date_of_birth is None and s >= SANCTIONS_MATCH_NO_DOB):
                _upsert_link(cur, f"person:{person_id}", f"sanction:{rid}", "SANCTIONS_MATCH",
                             [kyc_id, rid], score=round(s, 3))

    # --------------------------------------------------------------- logins
    def _logins(self, cur, rid: str, m) -> None:
        if not m.success:
            return  # a failed attempt may be someone else; it is kept as evidence only
        _upsert_link(cur, f"account:{m.account_id}", f"device:{m.device_id}", "LOGGED_IN_FROM",
                     rid, ts=m.ts)

    # --------------------------------------------------------- transactions
    def _transactions(self, cur, rid: str, m) -> None:
        cur.execute(
            """INSERT INTO transactions (txn_id, ts, from_account, to_account, amount_zar, channel, reference)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (rid, m.ts, m.from_account, m.to_account, m.amount_zar, m.channel, m.reference),
        )
        _upsert_link(cur, f"account:{m.from_account}", f"account:{m.to_account}", "SENT_TO",
                     rid, ts=m.ts, amount=m.amount_zar)

    # ----------------------------------------------------------- case notes
    def _case_notes(self, cur, rid: str, m) -> None:
        cur.execute(
            """INSERT INTO case_notes (note_id, account_id, author, created_at, text)
               VALUES (%s, %s, %s, %s, %s)""",
            (rid, m.account_id, m.author, m.created_at, m.text),
        )


def _trigram(a: str, b: str) -> float:
    """Python version of pg_trgm's similarity(), used when screening in bulk."""
    def grams(s: str) -> set[str]:
        out: set[str] = set()
        for word in s.split():
            w = f"  {word} "
            out.update(w[i:i + 3] for i in range(len(w) - 2))
        return out
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0

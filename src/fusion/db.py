"""PostgreSQL helpers: connect, apply the schema, reset for a clean run."""

from pathlib import Path

import psycopg

from fusion.config import settings

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def connect(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or settings.pg_dsn)


def has_pgvector(conn: psycopg.Connection) -> bool:
    row = conn.execute("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'").fetchone()
    return bool(row and row[0])


def apply_schema(conn: psycopg.Connection) -> None:
    """Create every table (safe to run twice: all statements use IF NOT EXISTS)."""
    conn.execute((SCHEMA_DIR / "01_schema.sql").read_text())
    if has_pgvector(conn):
        conn.execute((SCHEMA_DIR / "02_vectors.sql").read_text())
    else:
        # Same table without the vector column, for machines without pgvector.
        # Case-note search needs pgvector; everything else works without it.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS case_notes (
                note_id text PRIMARY KEY REFERENCES evidence (evidence_id),
                account_id text NOT NULL, author text NOT NULL,
                created_at timestamptz NOT NULL, text text NOT NULL)
        """)
    conn.commit()


def reset(conn: psycopg.Connection) -> None:
    """Empty every table so a demo or drill starts from zero."""
    conn.execute("""
        TRUNCATE evidence, processed_events, persons, accounts, sanctions,
                 links, transactions, case_notes CASCADE
    """)
    conn.execute("ALTER SEQUENCE person_seq RESTART WITH 1")
    conn.commit()


if __name__ == "__main__":
    import sys

    with connect() as c:
        if "--reset" in sys.argv:
            apply_schema(c)
            reset(c)
            print("PostgreSQL schema applied and all tables emptied.")
        else:
            apply_schema(c)
            print("PostgreSQL schema applied.")

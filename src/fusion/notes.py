"""Case notes: turn free text into vectors, then search them by meaning (RAG retrieval).

Embedding is a separate step from ingestion on purpose (ADR-0004): the
consumer stores notes immediately, and this job fills in vectors afterwards,
so a slow or unavailable model never blocks the event stream.

Two embedders:
  OllamaEmbedder  real semantic embeddings (nomic-embed-text, 768 dimensions)
  HashEmbedder    offline fallback: hashed word and character n-grams. It
                  only matches shared words, so it is NOT semantic; it exists
                  so tests and CI can run without a model.

Run:  python -m fusion.notes embed
      python -m fusion.notes search "someone was paid to receive money for another person"
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re

import httpx
import psycopg

from fusion.config import settings


class OllamaEmbedder:
    def __init__(self, url: str | None = None, model: str | None = None):
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.embed_model

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        # nomic-embed-text expects a task prefix: documents and queries are embedded differently.
        prefix = "search_query: " if kind == "query" else "search_document: "
        r = httpx.post(f"{self.url}/api/embed", json={"model": self.model,
                                                      "input": [prefix + t for t in texts]},
                       timeout=120)
        r.raise_for_status()
        return r.json()["embeddings"]


class HashEmbedder:
    def __init__(self, dim: int | None = None):
        self.dim = dim or settings.embed_dim

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            words = re.findall(r"[a-z]+", t.lower())
            feats = words + [w[i:i + 4] for w in words for i in range(max(1, len(w) - 3))]
            for f in feats:
                h = int(hashlib.md5(f.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def embedder():
    return HashEmbedder() if settings.embedder == "hash" else OllamaEmbedder()


def to_pgvector(v: list[float]) -> str:
    """pgvector's text format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def embed_pending(conn: psycopg.Connection, emb=None, batch: int = 32) -> int:
    """Fill in embeddings for notes that don't have one yet. Returns how many."""
    emb = emb or embedder()
    done = 0
    while True:
        rows = conn.execute(
            "SELECT note_id, text FROM case_notes WHERE embedding IS NULL ORDER BY note_id LIMIT %s",
            (batch,)).fetchall()
        if not rows:
            return done
        vectors = emb.embed([text for _, text in rows], kind="document")
        with conn.transaction():
            for (note_id, _), vec in zip(rows, vectors):
                conn.execute("UPDATE case_notes SET embedding = %s::vector WHERE note_id = %s",
                             (to_pgvector(vec), note_id))
        done += len(rows)


def search(conn: psycopg.Connection, query: str, k: int = 5, account_id: str | None = None,
           emb=None) -> list[dict]:
    """The k notes closest in meaning to `query` (cosine distance, lower = closer)."""
    emb = emb or embedder()
    qvec = to_pgvector(emb.embed([query], kind="query")[0])
    sql = """SELECT note_id, account_id, created_at, text, embedding <=> %s::vector AS distance
             FROM case_notes WHERE embedding IS NOT NULL"""
    params: list = [qvec]
    if account_id:
        sql += " AND account_id = %s"
        params.append(account_id)
    sql += " ORDER BY distance LIMIT %s"
    params.append(k)
    rows = conn.execute(sql, params).fetchall()
    return [{"note_id": n, "account_id": a, "created_at": c, "text": t, "distance": round(float(d), 4)}
            for n, a, c, t, d in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["embed", "search"])
    ap.add_argument("query", nargs="?")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()
    with psycopg.connect(settings.pg_dsn, autocommit=True) as conn:
        if args.command == "embed":
            n = embed_pending(conn)
            print(f"Embedded {n} case notes with {settings.embedder}:{settings.embed_model}")
        else:
            for hit in search(conn, args.query or "", args.k):
                print(f"{hit['distance']:.3f}  {hit['note_id']}  {hit['account_id']}  {hit['text'][:90]}")


if __name__ == "__main__":
    main()

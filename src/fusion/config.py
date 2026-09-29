"""Every setting in one place, read from environment variables.

Each value has a default that works with docker-compose.yml on a laptop,
so nothing needs configuring to get started. Override any of them in .env
(copied from .env.example) or as real environment variables.
"""

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from .env into the environment (real env vars win)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()



def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # Kafka-compatible event stream (Redpanda)
    kafka_bootstrap: str = _env("KAFKA_BOOTSTRAP", "localhost:19092")

    # PostgreSQL: entities, links, evidence, case-note vectors
    pg_dsn: str = _env(
        "PG_DSN", "postgresql://kgotla:kgotla@localhost:5432/fusion"
    )

    # ClickHouse: transaction events for fast aggregates
    ch_host: str = _env("CH_HOST", "localhost")
    ch_port: int = int(_env("CH_PORT", "8123"))
    ch_user: str = _env("CH_USER", "kgotla")
    ch_password: str = _env("CH_PASSWORD", "kgotla")
    ch_database: str = _env("CH_DATABASE", "fusion")

    # Which store answers transaction summaries: "clickhouse" or "postgres"
    txn_store: str = _env("TXN_STORE", "clickhouse")

    # The fusion API, as seen by the MCP server
    api_url: str = _env("FUSION_API_URL", "http://localhost:8000")

    # Local models through Ollama
    ollama_url: str = _env("OLLAMA_URL", "http://localhost:11434")
    chat_model: str = _env("CHAT_MODEL", "qwen2.5:7b")
    embed_model: str = _env("EMBED_MODEL", "nomic-embed-text")
    embed_dim: int = int(_env("EMBED_DIM", "768"))
    # "ollama" for real embeddings, "hash" for an offline lexical fallback
    embedder: str = _env("EMBEDDER", "ollama")

    # Where generated data and results live
    data_dir: str = _env("DATA_DIR", "data")
    results_dir: str = _env("RESULTS_DIR", "results")

    # OpenTelemetry (grafana/otel-lgtm listens on 4318 for OTLP over HTTP)
    otel_endpoint: str = _env("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    otel_enabled: bool = _env("OTEL_ENABLED", "true").lower() == "true"


settings = Settings()


# Topic names: one per source, plus one dead-letter topic for everything rejected
TOPICS = {
    "kyc": "raw.kyc",
    "transactions": "raw.transactions",
    "logins": "raw.logins",
    "sanctions": "raw.sanctions",
    "case_notes": "raw.case_notes",
}
DLQ_TOPIC = "dlq.rejected"

-- ClickHouse schema: transaction events for fast analytical aggregates.
-- The container creates the `fusion` database from CLICKHOUSE_DB, then runs this file.

CREATE TABLE IF NOT EXISTS fusion.transactions
(
    txn_id        String,
    ts            DateTime64(3, 'UTC'),
    from_account  String,
    to_account    String,
    amount_zar    Decimal(14, 2),
    channel       LowCardinality(String),
    reference     String
)
-- ReplacingMergeTree drops rows with the same sorting key when parts merge.
-- The sink is at-least-once, so a replay can insert a row twice; queries use
-- FINAL to see each transaction exactly once even before a merge happens.
ENGINE = ReplacingMergeTree
ORDER BY (to_account, ts, txn_id);

-- Same shape and engine, used only by the PostgreSQL-vs-ClickHouse benchmark.
CREATE TABLE IF NOT EXISTS fusion.bench_transactions AS fusion.transactions;

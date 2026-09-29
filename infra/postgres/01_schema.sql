-- PostgreSQL schema for the fused view of Kgotla's data.
-- Runs automatically the first time the postgres container starts
-- (files in /docker-entrypoint-initdb.d run in name order).

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy name matching (trigram similarity)

-- Every accepted source record, exactly as received. Its own id (KYC-000123,
-- T0012345, L0004567, S-083, N-00042) is the evidence id every answer cites.
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  text PRIMARY KEY,
    source       text NOT NULL,
    payload      jsonb NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now()
);

-- Idempotency ledger: one row per processed event. Re-delivered events hit
-- the primary key and are skipped, so replaying the stream changes nothing.
CREATE TABLE IF NOT EXISTS processed_events (
    event_key     text PRIMARY KEY,           -- '<source>:<record id>'
    processed_at  timestamptz NOT NULL DEFAULT now()
);

-- Resolved real-world people. One person can own several accounts.
CREATE SEQUENCE IF NOT EXISTS person_seq;
CREATE TABLE IF NOT EXISTS persons (
    person_id      text PRIMARY KEY DEFAULT 'P-' || lpad(nextval('person_seq')::text, 6, '0'),
    display_name   text NOT NULL,
    name_norm      text NOT NULL,
    date_of_birth  date NOT NULL,
    city           text,
    created_from   text NOT NULL REFERENCES evidence (evidence_id)
);
CREATE INDEX IF NOT EXISTS persons_name_trgm ON persons USING gin (name_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS persons_dob ON persons (date_of_birth);

CREATE TABLE IF NOT EXISTS accounts (
    account_id       text PRIMARY KEY,
    person_id        text NOT NULL REFERENCES persons (person_id),
    full_name        text NOT NULL,
    name_norm        text NOT NULL,
    phone_e164       text NOT NULL,
    email            text,
    city             text,
    opened_at        timestamptz,
    kyc_evidence_id  text NOT NULL REFERENCES evidence (evidence_id),
    matched_rule     text NOT NULL   -- NEW, R1 (phone + DOB), R2 (e-mail) or R3 (name + DOB + city)
);
CREATE INDEX IF NOT EXISTS accounts_phone ON accounts (phone_e164);
CREATE INDEX IF NOT EXISTS accounts_email ON accounts (lower(email));
CREATE INDEX IF NOT EXISTS accounts_name_trgm ON accounts USING gin (name_norm gin_trgm_ops);

CREATE TABLE IF NOT EXISTS sanctions (
    entry_id       text PRIMARY KEY REFERENCES evidence (evidence_id),
    full_name      text NOT NULL,
    name_norm      text NOT NULL,
    aliases_norm   text[] NOT NULL DEFAULT '{}',
    date_of_birth  date,
    list_name      text NOT NULL
);
CREATE INDEX IF NOT EXISTS sanctions_name_trgm ON sanctions USING gin (name_norm gin_trgm_ops);

-- The graph. Node ids are typed strings: 'person:P-000001', 'account:A000123',
-- 'phone:+27821234567', 'device:D-1a2b3c4d', 'sanction:S-083'.
-- One row per (src, dst, kind); repeated events raise the weight and add
-- evidence ids (the first 20 are kept, enough to cite).
CREATE TABLE IF NOT EXISTS links (
    src           text NOT NULL,
    dst           text NOT NULL,
    kind          text NOT NULL,   -- OWNS, REGISTERED_PHONE, LOGGED_IN_FROM, SENT_TO, SANCTIONS_MATCH
    weight        integer NOT NULL DEFAULT 1,
    total_zar     numeric(16, 2),
    score         real,
    first_seen    timestamptz,
    last_seen     timestamptz,
    evidence_ids  text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS links_dst ON links (dst);

-- Transactions are also kept here, so the same aggregate can be timed
-- against PostgreSQL and ClickHouse (docs/benchmark.md).
CREATE TABLE IF NOT EXISTS transactions (
    txn_id        text PRIMARY KEY,
    ts            timestamptz NOT NULL,
    from_account  text NOT NULL,
    to_account    text NOT NULL,
    amount_zar    numeric(14, 2) NOT NULL,
    channel       text NOT NULL,
    reference     text
);
CREATE INDEX IF NOT EXISTS transactions_from ON transactions (from_account, ts);
CREATE INDEX IF NOT EXISTS transactions_to ON transactions (to_account, ts);

# Engineering log

This log records the issues I found while building the platform, how I investigated them, and what I changed. It is organised by milestone. Each milestone ends with a checkpoint: the state of the system that I verified before building on it.

---

## Milestone 1: Platform foundation

**Goal:** a reproducible local environment for the fraud data fusion platform: event streaming, operational and analytical databases, observability, and a local language model. It had to start with one command and run on a standard 8 GB developer laptop.

### Checkpoint

| Component | Verified state |
|---|---|
| Redpanda (event streaming) + Console | running and healthy; broker reachable on `localhost:19092` |
| PostgreSQL 17 + pgvector | healthy; `fusion` and `fusion_test` databases created on first start |
| ClickHouse | healthy; HTTP interface on `localhost:8123`; tuned for low-memory hosts (see below) |
| Grafana LGTM (traces, logs, metrics) | healthy; stopped when not in use to save memory |
| Local models (Ollama) | `qwen2.5:3b` for chat at 11.18 tokens/s on CPU; `nomic-embed-text` returns 768-dimension embeddings |
| Configuration | typed, immutable settings loaded from environment variables with a `.env` fallback; 5 unit tests passing |
| Code quality | `ruff` lint clean |

### Issue: language model generation at 0.03 tokens per second

- **Impact:** a one-sentence answer took 20 min 41 s (model load 6 min 10 s, generation 0.03 tokens/s). At that speed the investigation agent would be unusable.
- **Investigation:**
  1. `ollama ps` showed the model loaded and running on CPU, so the process was working, only slowly.
  2. `free -h` inside WSL showed 1.9 GiB available and 2.2 GiB in swap: memory was being paged to disk.
  3. Stopping the observability container raised available memory only from 1.9 to 2.0 GiB, so it wasn't the main consumer.
  4. `docker stats` showed the four remaining containers using about 630 MB in total, so they weren't the main consumer either.
  5. Windows Task Manager showed 669 MB available and 25.0 of 29.3 GB committed. With 6 GB of the laptop's 8 GB assigned to WSL, Windows was paging the Linux VM's memory to its own page file: two layers of swapping.
- **Root cause:** memory pressure across the whole host, not the model or the CPU.
- **Resolution:** with the containers stopped, available memory in WSL rose to 4.4 GiB and the same prompt generated at **11.18 tokens/s** (answer in under 3 s), roughly 370 times faster.
- **Prevention:** I run only the services each workload needs. Data ingestion runs the full stack without the chat model loaded; the investigation agent runs PostgreSQL, ClickHouse, the API and the model, without the streaming and observability services.
- **Takeaway:** when performance is off by orders of magnitude, check memory and swap on the host as well as inside the VM before looking at the code.

### Issue: ClickHouse consuming CPU and disk while idle

- **Impact:** with no queries running, ClickHouse used 53% of a CPU core and had read 2.75 GB and written 2.13 GB, competing with the language model for CPU and disk.
- **Root cause:** by default ClickHouse records its own activity (queries, per-second metrics, stack samples, merges) in `system.*_log` tables. The platform doesn't use them.
- **Resolution:** `infra/clickhouse-config/low-resource.xml` removes those log tables and is mounted read-only into `/etc/clickhouse-server/config.d/`. After a restart the container wrote about 2 MB in four minutes, and CPU settled at about 11% of one core, roughly 1.4% of the 8-core host.
- **Takeaway:** database defaults are tuned for dedicated servers; on shared or small hosts, switch off what the workload doesn't use. `docker stats` reports CPU per core, not per machine.

### Minor setup issues

| Symptom | Cause | Resolution |
|---|---|---|
| `docker compose up` failed: service refers to undefined volume `redpanda-data` | named volumes must be declared at the top level of the compose file | added a top-level `volumes:` block for `redpanda-data`, `pg-data` and `ch-data` |
| `ruff` reported `W292 No newline at end of file` | the editor saved files without a final newline | `ruff check --fix`; enabled "Insert Final Newline" in the editor so it doesn't recur |
| first `git push` rejected: no upstream branch | new local branch not yet linked to the remote | `git push -u origin main`; set `push.autoSetupRemote` for future branches |
| terminal stopped responding to input | Ctrl+S sends XOFF (pause output) in a Linux terminal | Ctrl+Q resumes it; added `stty -ixon` to `~/.bashrc` to disable flow control |

---

## Milestone 2: Synthetic data and validation

**Goal:** a realistic, fully synthetic data set for five sources, with a planted fraud ring whose ground truth is known, plus a validation gate that rejects malformed or implausible records with a stated reason.

### Checkpoint

| Component | Verified state |
|---|---|
| Record schemas | one strict Pydantic model per source; unknown fields rejected |
| Normalisation | one definition of "same phone" (E.164) and "same name"; 12 unit tests |
| Validation gate | schema and plausibility checks; every rejection returns a reason; 14 unit tests |
| Generator (seed 42) | 72,395 records published: KYC 5,379, transactions 50,250, logins 16,261, sanctions 200, case notes 305 |
| Planted ring | controller with 4 accounts on 2 phones, 8 mules, 2 shared devices, 1 sanctions near-match (S-044), 5 case notes written without the words "mule" or "ring" |
| Decoys | a household sharing a tablet; a spaza shop with many payers and no onward flow |
| Deliberate damage | 2,157 damaged records and 250 duplicate transfers, all listed in the ground truth |
| Proof | the gate rejects exactly the damaged records, no more and no fewer; ring accounts are never damaged; same seed produces the same data. 36 tests passing |

No issues were found during this milestone.

---

## Milestone 3: Event streaming

**Goal:** publish every source as an event stream, one topic per source, with a delivery contract that loses nothing and doubles nothing, and with tracing hooks built in from the start.

### Checkpoint

| Component | Verified state |
|---|---|
| Stream layer | one module owns all Kafka client code; the rest of the pipeline depends only on `send`/`poll`/`commit` |
| Delivery contract | consumers commit only after storing (at-least-once); idempotent producer with `acks=all` |
| In-memory bus | behaves like a consumer group: resumes from the last commit, groups independent, values copied; 4 tests |
| Topics | `raw.kyc`, `raw.transactions`, `raw.logins`, `raw.sanctions`, `raw.case_notes` (one partition each) and `dlq.rejected` |
| Publish | 72,395 events published in source order (sanctions first); broker high-watermark for `raw.transactions` confirmed at 50,250 |
| Telemetry | tracing and metrics hooks that become no-ops when disabled, so the platform runs with the observability stack stopped |
| Tests | 43 passing |

No issues were found during this milestone.

---

## Milestone 4: Entity resolution and the evidence graph

**Goal:** consume every stream, validate each record, and resolve accepted records into people, accounts and an evidence-backed link graph in PostgreSQL, exactly once, surviving crashes, replays and poison records.

### Checkpoint

| Component | Verified state |
|---|---|
| Schema | evidence store, idempotency ledger, persons, accounts, sanctions, link graph, transactions, case notes (pgvector column ready) |
| Merge rules | R1 phone + date of birth, R2 e-mail, R3 name similarity >= 0.6 + date of birth + city; every account records the rule that placed it |
| Link, not merge | shared phones and devices become graph links between people, never merges |
| First full run | 72,395 events in 428.0 s (about 170 events/s): 69,988 applied, 250 duplicates, 2,157 rejected to the dead-letter topic, 0 poison |
| Resolution | 5,218 accounts into 4,866 persons; the controller's 4 accounts resolve to 1 person; sanctions near-match on the controller found |
| Independent verification | `python -m fusion.verify`: 13 checks against the ground truth, all passing |
| Poison drill | a record that passes validation but cannot be stored: 3 attempts, parked as `poison`, stream continued |
| Replay + crash drill | full data set re-published, consumer killed with SIGKILL mid-stream and restarted: 0 new rows, 0 lost, all checks passing |
| Tests | 48 passing, including 4 pipeline integration tests against PostgreSQL (crash, poison, replay, exactly-once) |

### Issue: restarted consumer exited without processing after a hard kill

- **Impact:** after `kill -9` mid-stream, the restarted consumer reported success in 15.1 s having processed nothing. 35,377 messages were left unread on `raw.transactions`. The data guarantees held, but processing silently stopped: in production, lag would grow with nothing alerting.
- **Investigation:**
  1. The restart's summary showed zero messages in every column, and "Finished in 15.1 s" matched the `--idle-exit 15` setting exactly: the consumer had idled for the whole run.
  2. `rpk group describe fusion-resolver` showed TOTAL-LAG 35,377, all on `raw.transactions`, so messages were waiting.
  3. The group was `Empty` with 0 members by the time I checked, which pointed to the killed member having held the partitions until it expired.
- **Root cause:** a consumer killed without leaving its group stays a member until its session timeout (45 s by default in the client). Until then its partitions aren't reassigned, so the new consumer receives nothing, and the idle-exit logic counted that wait as "the stream is empty".
- **Resolution:**
  - The consumer only starts its idle clock once the group has assigned it partitions (`assigned()` added to the consumer interface; the in-memory bus always reports assigned).
  - `session.timeout.ms` lowered from 45 s to 10 s, so a crashed member is replaced faster.
  - A regression test (`tests/test_consumer_idle.py`) reproduces the scenario with a stub consumer. It failed before the fix (`assert 0 == 1`) and passes after.
- **Verification:** I repeated the drill with the same `--idle-exit 15`. The restarted consumer waited for assignment, then processed the remaining 61,395 messages in 102.2 s. Lag 0, all checks passing.
- **Takeaway:** "no messages" and "not yet assigned" look identical from inside a poll loop. Check the lag from the broker's side after every recovery, not just the consumer's own summary.

### Observations

- Replayed (duplicate) events process at about 600-700 per second, against about 170 per second for first-time events: a duplicate costs one ledger lookup, while a new KYC record also runs entity matching and sanctions screening.
- The full test suite takes about 4 minutes on this laptop, almost all of it in the PostgreSQL integration tests. In CI I'd run unit and integration tests as separate jobs.

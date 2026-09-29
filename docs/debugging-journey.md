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

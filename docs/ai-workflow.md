# AI-assisted development

I use an AI assistant throughout this project as a pair programmer. This page records how I use it, how I check its output, and where it was wrong. It is updated at each milestone.

## Tools

- Claude (Anthropic), used conversationally: design discussion, code drafts, explanations of unfamiliar libraries, and help diagnosing problems.

## How I work with it

1. **The running system is the judge.** AI output is a proposal until a test, a linter or a real run confirms it.
2. **I don't commit what I can't explain.** I go through generated code line by line, and I make sure I understand each part before it goes in.
3. **Measure before accepting a diagnosis.** When the assistant suggests a cause, I check it with a command (`free -h`, `docker stats`, logs) before acting on it.
4. **Destructive steps are mine.** Deleting volumes, dropping data or rewriting history: I decide and run those myself.
5. **Real numbers only.** Every figure in this repository comes from a run on my machine.

---

## Milestone 1: Platform foundation

### What I delegated
- First drafts of the typed configuration module and its unit tests, the Docker Compose file, and the Makefile.
- Explanations of Compose health checks, Redpanda's internal and external listeners, and PostgreSQL first-start initialisation scripts.
- Suggestions for diagnosing slow model inference.

### Where it was wrong, and how I caught it
- **Generated Compose file wouldn't start.** It referenced a named volume without declaring it at the top level. `docker compose up` rejected it with `refers to undefined volume redpanda-data`; I added the missing `volumes:` block.
- **Wrong first diagnosis of the slow model.** The assistant's first suggestion was that the containers were taking the memory, and that stopping the observability stack would fix it. I measured instead: stopping it raised available memory only from 1.9 to 2.0 GiB, and `docker stats` showed all containers together using about 630 MB. Windows Task Manager showed the real cause, host-level memory pressure (669 MB available, 25 of 29.3 GB committed). The fix followed from the measurement, not the first guess. Details are in `docs/debugging-journey.md`.

### Where my judgement was needed
- Choosing the 3B model over the 7B default for an 8 GB machine, and deciding which services run together for each workload.
- Deciding to turn off ClickHouse's internal logging after seeing it use CPU and disk while idle, and checking that nothing in the platform depends on those tables.

---

## Milestone 4: Entity resolution and the evidence graph

### What I delegated
- First drafts of the schema, the resolver, the consumer, the verification script, the failure drills and the pipeline tests.
- Explanations of consumer-group offsets, idempotent writes and PostgreSQL trigram similarity.

### Where it was wrong, and how I caught it
- **The consumer's idle-exit logic didn't account for group rebalancing.** The drafted loop treated any silent period as "the stream is finished". My crash drill (SIGKILL mid-stream, then restart) showed the restarted consumer exiting after 15 seconds with 35,377 messages unread. I confirmed it from the broker with `rpk group describe`, reproduced it in a failing test, then fixed it: the idle clock only starts once partitions are assigned, and a shorter session timeout. The drill passed after the fix. Details are in `docs/debugging-journey.md`.

### Where my judgement was needed
- Treating an all-green `verify` result with suspicion: the first drill passed every data check while processing had silently stopped. I only found it by comparing the numbers in the consumer's summary with the broker's lag.
- Choosing a 10-second session timeout: short enough to recover quickly after a crash, long enough not to evict a consumer that's briefly slow on an 8 GB machine.

---

## Milestone 5: Analytical store and semantic search

### What I delegated
- First drafts of the ClickHouse schema, the sink, the per-account summary query and the embedding and search module.

### What I checked or changed
- The drafted sink had the same idle-exit loop that failed in the Milestone 4 crash drill. I applied the assignment-aware fix before running it, instead of waiting for it to fail again.
- The prediction that the poison record would make the stores disagree by exactly one row was confirmed by the count (48,501). I chose drill cleanup over coupling the consumers; the reasoning is in `docs/debugging-journey.md`.
- I scored the semantic search myself against the planted notes (4 of 5 in the top 5 for each question), rather than judging it by eye.

---

## Milestone 6: Read-only API

### What I delegated
- First drafts of the response models, the repository, the endpoints and the contract tests.

### Where it was wrong, and how I caught it
- **The network ordering dropped the most important links under truncation.** The drafted query sorted by weight and amount, which pushed ownership and phone links to the end. I only saw it by reading the live response for a real mule (link kinds present: `LOGGED_IN_FROM` and `SENT_TO` only). I reproduced it with a failing test, then changed the ordering so identity links come first.
- **The assistant's expectations were wrong twice in this milestone:** it expected `OWNS` and `REGISTERED_PHONE` in that network response, and it expected a single match from my write-keyword search of the repository (there were five, four of them the Python variable `truncated`). Reading the actual output, not the expected output, caught both.

---

## Milestone 7: MCP tools

### What I delegated
- First drafts of the MCP server, its tests and the protocol smoke-test client.

### What I checked
- I read each tool description as the model would see it, since the model chooses tools from the name, parameters and description alone.
- I confirmed the MCP server holds no database access: it only knows the API's address.
- I verified the tools through the real protocol with a separate client, not only by calling the Python functions directly.

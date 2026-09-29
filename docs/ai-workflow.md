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

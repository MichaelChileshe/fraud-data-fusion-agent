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

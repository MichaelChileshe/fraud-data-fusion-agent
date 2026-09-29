# Shortcuts for the commands used most. Run `make help` for the list.
PY ?= python

.PHONY: help up down nuke data produce consume sink embed api mcp ask eval bench drill-poison drill-replay verify test lint reset diagram

help:
	@grep -E '^[a-z-]+:.*## ' Makefile | awk -F':.*## ' '{printf "  make %-13s %s\n", $$1, $$2}'

up: ## start Redpanda, PostgreSQL, ClickHouse, Grafana (waits until healthy)
	docker compose up -d --wait

down: ## stop the containers (data kept)
	docker compose down

nuke: ## stop the containers AND delete all their data
	docker compose down -v

data: ## generate the synthetic data set and ground truth
	$(PY) -m fusion.generate

produce: ## publish the data set onto the event stream
	$(PY) -m fusion.produce

consume: ## resolve everything into PostgreSQL (exits when idle)
	$(PY) -m fusion.consumer --idle-exit 15

sink: ## copy transactions into ClickHouse (exits when idle)
	$(PY) -m fusion.warehouse sink --idle-exit 15

embed: ## embed case notes with Ollama into pgvector
	$(PY) -m fusion.notes embed

api: ## run the fusion API on :8000 (docs at /docs)
	uvicorn fusion.api.main:app --port 8000 --reload

mcp: ## serve the MCP tools over HTTP on :8765 (for the MCP Inspector)
	$(PY) -m fusion.mcp_server --http --port 8765

ask: ## ask the agent something: make ask Q="..."
	$(PY) -m fusion.agent.run "$(Q)"

verify: ## check every store against the ground truth
	$(PY) -m fusion.verify --clickhouse

eval: ## run the 20 golden questions
	$(PY) -m fusion.eval.run_eval

bench: ## PostgreSQL vs ClickHouse at 50k and 1M rows
	$(PY) -m fusion.bench

drill-poison: ## publish one record that passes the gate but can't be stored
	$(PY) -m fusion.drills poison

drill-replay: ## publish the whole data set a second time (nothing should change)
	$(PY) -m fusion.drills replay

test: ## unit + integration tests
	pytest

lint: ## ruff
	ruff check src tests scripts

reset: ## empty PostgreSQL tables for a fresh run
	$(PY) -m fusion.db --reset

diagram: ## redraw docs/images/architecture.png
	$(PY) scripts/draw_architecture.py

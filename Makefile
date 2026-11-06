.DEFAULT_GOAL := help
ORCHESTRATOR := services/orchestrator

.PHONY: help sync lint types test test-gateway test-llm-gateway test-orchestrator test-int smoke check up down logs live-check

help: ## Show these commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-20s %s\n", $$1, $$2}'

sync: ## Install every project's dependencies
	uv sync --all-packages
	cd $(ORCHESTRATOR) && uv sync

lint: ## Check formatting and lint rules
	uv run ruff format --check .
	uv run ruff check .

types: ## Run the type checker on both lockfile groups
	uv run pyright
	cd $(ORCHESTRATOR) && uv run pyright

test-gateway: ## Run gateway tests
	uv run --package gateway pytest services/gateway/tests

test-llm-gateway: ## Run llm-gateway tests
	uv run --package llm-gateway pytest services/llm-gateway/tests/unit services/llm-gateway/tests/contract

test-orchestrator: ## Run orchestrator tests
	cd $(ORCHESTRATOR) && uv run pytest

test: test-gateway test-llm-gateway test-orchestrator ## Run all fast tests, with no network and no live Claude calls

test-int: ## Run tests against a real Postgres (starts one in Docker)
	uv run --package llm-gateway pytest services/llm-gateway/tests/integration --force-enable-socket

smoke: ## Build the stack and run the black-box Compose tests
	uv run pytest tests/e2e

check: lint types test ## Run everything CI runs before the smoke test

up: ## Start the local stack and wait until it is healthy
	docker compose up --build --detach --wait

down: ## Stop the local stack and remove its volumes
	docker compose down --volumes --remove-orphans

live-check: ## Call Claude once through the running stack. Spends money only in real mode
	docker compose exec -T orchestrator /app/services/orchestrator/.venv/bin/python -m orchestrator.live_check

logs: ## Follow logs from the local stack
	docker compose logs --follow

# Scout

Scout is a vulnerability triage assistant. Given a software bill of materials, it finds the
affected components, decides what to fix first and explains why. It is a defensive tool: it
helps people decide what to patch, and it neither writes nor collects exploit code.

**Status:** Day 3 of 40. The services, CI, local stack, `llm-gateway` and one hand-written agent
loop exist.

## Quick start

You need [uv](https://docs.astral.sh/uv/), Docker and Make.

```bash
make sync    # install dependencies
make check   # lint, types and fast tests, with no network and no Claude calls
make up      # build and start the stack, then wait until it is healthy
curl http://127.0.0.1:8000/readyz
make down
```

Run `make help` to see every command. By default the stack uses a fake Claude, so it costs nothing.

## Calling the real Claude

1. Copy `.env.example` to `.env`. Git ignores `.env`, so your key stays on your machine.
2. In `.env`, set `LLM_GATEWAY_MODE=real` and `ANTHROPIC_API_KEY=<your key>`.
3. Run `make up`, then `make live-check`. This makes two very small Claude calls and prints the
   text, tokens and cost.

Run `make agent-demo` to see the Day 3 agent use two tools, with every message printed.

`llm-gateway` refuses further calls once the day's spend reaches `LLM_DAILY_LIMIT_USD`.

## Layout

| Path | What it holds |
| --- | --- |
| `libs/contracts` | Pydantic models shared across services |
| `libs/testkit` | Test doubles, including `FakeClaude` |
| `services/gateway` | Public entry point |
| `services/llm-gateway` | Holds the Claude key; handles cost, retries and the daily limit |
| `services/orchestrator` | Main agent engine on LangChain and LangGraph, with its own lockfile |
| `deploy/postgres` | Database setup for local Compose |
| `tests/e2e` | Black-box tests against the Compose stack |
| `docs/adr` | Decision records |
| `docs/learning` | Plain-language notes on each day's main idea |

## Working rules

- Each change starts with a failing test: a `test:` commit, then a `feat:` commit.
- Fast tests cannot open network sockets or call Claude.
- All internal REST APIs use FastAPI.
- Agents get their model from `orchestrator.llm.build_chat_model`, which allows no sampling
  parameters.

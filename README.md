# Scout

Scout is a vulnerability triage assistant. Given a software bill of materials, it finds the
affected components, decides what to fix first and explains why. It is a defensive tool: it
helps people decide what to patch, and it neither writes nor collects exploit code.

**Status:** Day 1 of 40. The service skeletons, CI and local stack exist. There are no agents yet.

## Quick start

You need [uv](https://docs.astral.sh/uv/), Docker and Make.

```bash
make sync    # install dependencies
make check   # lint, types and fast tests, with no network and no Claude calls
make up      # build and start the stack, then wait until it is healthy
curl http://127.0.0.1:8000/readyz
make down
```

Run `make help` to see every command.

## Layout

| Path | What it holds |
| --- | --- |
| `libs/contracts` | Pydantic models shared across services |
| `services/gateway` | Public entry point |
| `services/orchestrator` | Main agent engine on LangChain and LangGraph, with its own lockfile |
| `tests/e2e` | Black-box tests against the Compose stack |
| `docs/adr` | Decision records |

## Working rules

- Each change starts with a failing test: a `test:` commit, then a `feat:` commit.
- Fast tests cannot open network sockets or call Claude.
- All internal REST APIs use FastAPI.
- Agents get their model from `orchestrator.llm.build_chat_model`, which allows no sampling
  parameters.

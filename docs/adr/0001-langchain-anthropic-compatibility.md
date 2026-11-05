# ADR 0001: langchain-anthropic works with the new Claude SDK, with three guards

**Status:** Accepted, pending one live call on Day 2
**Date:** 2026-11-05

## Question

Scout is built on LangChain and LangGraph, and API version 2026-10-21 changed how Claude
requests work. Does `langchain-anthropic` work with `anthropic` 2.x and that API version?

## Method

`tests/contract/test_chat_anthropic_wire.py` runs the real `ChatAnthropic` against a fake
Anthropic API on 127.0.0.1 and inspects each HTTP request. No live Claude calls were made,
because no API key was available. The fake API's responses were written by hand, so this
checks the request Scout sends and how Scout parses a response of the expected shape. It
does not prove the real API accepts the request.

## Versions tested

| Package | Version |
| --- | --- |
| anthropic | 2.5.0 |
| langchain | 1.4.9 |
| langchain-anthropic | 1.7.10 |
| langchain-core | 1.7.6 |
| langgraph | 1.2.20 |

## Results

| Check | Result |
| --- | --- |
| Packages resolve together with `anthropic` 2.x | Yes |
| `anthropic-version` header | `2026-10-21` |
| Key sent as `Authorization: Bearer`, with no `x-api-key` | Yes |
| Custom base URL honored, so `llm-gateway` can sit in between | Yes |
| No sampling parameters sent by default | Yes, on Haiku, Sonnet and Opus 5.5 |
| Tool calls and stop reason parsed | Yes |
| Streaming text and stop reason | Yes |

## Findings that need guards

1. **LangChain does not block sampling parameters on every model.** With `temperature=0`,
   `ChatAnthropic` raises an error for `claude-sonnet-5-5` but sends the parameter for
   `claude-haiku-5-5` and `claude-opus-5-5`. API version 2026-10-21 is documented to answer
   that with a 400.
   - **Guard now:** `build_chat_model` accepts no sampling arguments.
   - **Guard on Day 2:** `llm-gateway` rejects any request that carries them.
2. **LangChain's token counts differ from the API's.** `usage_metadata["input_tokens"]` adds
   cached tokens: the fake API returned 10 input, 7 cache-read and 3 cache-write tokens, and
   LangChain reported 20.
   - **Guard on Day 2:** cost accounting lives in `llm-gateway` and reads raw `usage`.
3. **The default `max_tokens` is very large.** With none set, `ChatAnthropic` sent 128,000
   for `claude-sonnet-5-5`, which is a budget risk.
   - **Guard now:** `build_chat_model` always sets it, with a default of 4,096.

## Open questions

- **Streaming usage:** when the fake API's `message_delta` carried only `output_tokens`,
  LangChain's merged stream usage showed 0 input tokens, even though `message_start` had them.
  What the real API sends has not been checked, so `llm-gateway` should read usage from the
  stream itself. Check this with a live call on Day 2.
- **Live call:** make one real call through `ChatAnthropic` once `llm-gateway` has a key.
- **MCP:** `langchain[mcp]` and `MCPAdapter` are not installed or tested yet. Check them on
  Day 7. The `fastmcp` it depends on should be resolved alongside the `mcp` 2.x SDK.

## Decision

Keep the LangChain-first plan, with no need to pin an older SDK. Agents must get their model
from `build_chat_model`. These tests run on every push, so a framework upgrade that changes
this behavior fails CI.

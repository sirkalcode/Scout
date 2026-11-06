# ADR 0002: llm-gateway controls the Claude key and spend

Status: accepted, 2026-11-05

## Decision

All Claude calls go through `llm-gateway`, which exposes the same `/v1/messages` API as Anthropic.
It is the only service with the Claude key. Agents use a separate internal token.

- **Pass-through:** request and stream bytes are forwarded unchanged.
- **Cost:** worked out from raw usage fields, because `usage_metadata` adds cached tokens to input
  and would overcharge (see ADR 0001). `pricing.py` cites the official price page.
- **Daily limit:** a call first reserves its worst case: `max_tokens` at the output rate, plus one
  token per two body bytes at the 1-hour cache-write rate. Over the limit returns 402
  `billing_error`, which SDKs do not retry.
- **Failing closed:** if spend cannot be read, calls are refused with 503. If spend cannot be saved,
  it still counts in memory until the process restarts.
- **Retries:** 429, 5xx, 529 and connection errors get three attempts. A `retry-after` over 60
  seconds goes back to the caller. Streams retry only until the first byte reaches the caller.
- **Dropped streams:** the caller gets an SSE `error` event. Spend is recorded with output estimated
  at two characters per token and marked `estimated`.
- **Fake mode:** the default. `FakeClaude` replaces the network, so local work and smoke tests cost
  nothing. `compose.test.yaml` forces it even if `.env` holds a real key.

## Known limits

- Reservations live in one process, so this service must run as a single replica. AWS on Day 36
  keeps one task, or moves reservations to Postgres.
- Requests with `speed` or `inference_geo` are refused until the cost math covers them.
- Web search is charged afterwards at $0.01 per search but is not in the reservation, so a request
  using it can pass the limit by a small amount.
- A stream that fails before `message_start` records nothing, because no usage is known.

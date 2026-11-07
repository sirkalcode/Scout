# Day 3: the agent loop, by hand

An agent is a model plus tools. Claude never runs a tool. It asks for one, your code runs it and
sends back the result, and this repeats until Claude gives an answer. That repetition is the
**agent loop**. The code is in `services/orchestrator/src/orchestrator/hand_loop/`.

## Try it

```bash
make up
make agent-demo                                    # the default Log4j question
make agent-demo Q="Is CVE-2022-22965 urgent?"      # your own question
make agent-demo MODEL=claude-sonnet-5-5            # a larger model
```

In fake mode the answer is canned. In real mode a run with Haiku costs well under one cent.

## The messages in one run

| # | Role | Content | Why |
| --- | --- | --- | --- |
| 1 | user | The question | Starts the conversation. The tool definitions go with every request. |
| 2 | assistant | `tool_use check_kev`, `stop_reason: tool_use` | Claude needs a fact first. |
| 3 | user | `tool_result` for that `tool_use` id | Your code ran the tool. Results always go in a **user** message. |
| 4 | assistant | `tool_use lookup_cve` | Claude needs another fact. |
| 5 | user | `tool_result` | |
| 6 | assistant | Text, `stop_reason: end_turn` | The answer. The loop stops. |

Every request resends the whole conversation, because the Messages API keeps no memory between
calls. Thinking blocks go back unchanged, signature included.

## Rules the tests pin down

- **`tool_use` → run the tools.** All tool calls in one reply get their results in one user
  message, in the same order, matched by `tool_use_id`.
- **`pause_turn` → send the conversation back** with no tool results, so Claude can carry on.
- **Anything else ends the run:** `end_turn`, `max_tokens`, `refusal` and the rest are reported,
  not hidden.
- **`max_steps`** stops an agent that never finishes. Each step is a paid call.
- **Failures become `is_error: true` results, never crashes.** An unknown tool, bad input, an
  expected `ToolError` and an unexpected exception all go back to Claude so it can change course.
  Unexpected exceptions show only their type, because their text can hold secrets or paths.
- **Strict tool use.** Each tool's input is a Pydantic model. `strict: true` makes Claude's input
  match the schema the API sees. That schema cannot express every rule, so the SDK moves
  `pattern` into the description, and the Pydantic model checks it again before the tool runs.

## The same agent on the SDK's Tool Runner

`tool_runner.py` hands the loop to `client.beta.messages.tool_runner`. A test shows both send
identical requests.

| | Hand-written loop | Tool Runner |
| --- | --- | --- |
| The loop, dispatch, `is_error` | You write them (about 70 lines) | Built in |
| Step limit | `max_steps` | `max_iterations` |
| Input checks | Your Pydantic model | The function's type hints |
| Unexpected exception text | Type only | Full `repr`, sent to the model |
| History | Resends everything | Uses the **message-threads beta** by default: Anthropic stores the conversation, and later requests send only the new tool results. `thread=False` turns this off. |
| Seeing each message | `on_message` callback | Iterate the runner |

The threads default was a surprise. It sends less data, but the conversation then lives on
Anthropic's side, and `llm-gateway`'s worst-case reservation, which is based on request size,
underestimates such requests. The ledger is still exact, because it uses reported usage. The
Day 5 message-threads check decides whether Scout uses threads.

Day 4 hands the same loop to LangChain's `create_agent`. Day 34 grows this one into a full engine
and compares the two.

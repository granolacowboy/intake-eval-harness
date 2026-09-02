# intake-eval-harness

A small, deterministic **evaluation harness for MCP servers** — run a fixed set of task questions against a Model Context Protocol server, let a model use its tools, and score the results against expectations. Extracted from [`intake-triage-mcp`](https://github.com/granolacowboy/intake-triage-mcp), where it gates a legal-intake triage server.

> Built on a simple principle: an AI capability isn't "done" because it demoed — it's done when it **passes an eval**. This is the harness that enforces that for tool-using MCP servers.

## What it does

- Connects to an MCP server (stdio or HTTP) via `connections.py`.
- Runs each question in an eval suite through a model that must use the server's tools.
- Captures the tool calls and the final answer, and scores them against expected tool usage / output.
- Emits machine-readable results (JUnit-style XML) so it drops straight into CI.

## Why it exists

Tool-using systems fail in ways a unit test doesn't catch: the model picks the wrong tool, skips a required gate, or hallucinates an answer instead of calling anything. This harness makes those failures **visible and regression-tested** — the difference between "applied AI in production" and a demo.

## Layout

```
evals/
  evaluation.py     # the harness: load suite -> run each case -> score -> emit XML
  connections.py    # MCP transport (stdio / HTTP) setup
  requirements.txt  # anthropic + mcp
```

## Quick start

```bash
pip install -r evals/requirements.txt
export ANTHROPIC_API_KEY=...          # the judge/agent model
python evals/evaluation.py --help     # point it at your MCP server + a suite
```

Define your suite as questions with expected tool usage, then run it in CI on every change to your server.

## Related

- **[intake-triage-mcp](https://github.com/granolacowboy/intake-triage-mcp)** — the deterministic legal-intake MCP server this harness was built to gate.
- **[llm-security-for-law-firms](https://github.com/granolacowboy/llm-security-for-law-firms)** — the security/adoption checklist that pairs with evaluation-driven delivery.

---

<sub>Maintained by [Rich Berman](https://github.com/granolacowboy) / [MHSB Solutions](https://github.com/MHSBai). MIT licensed. Contributions welcome.</sub>

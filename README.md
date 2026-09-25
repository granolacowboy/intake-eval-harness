# intake-eval-harness

[![Verified](https://github.com/granolacowboy/granolacowboy.dev/actions/workflows/verify-intake-eval-harness.yml/badge.svg)](https://github.com/granolacowboy/granolacowboy.dev/actions/workflows/verify-intake-eval-harness.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A small evaluation harness for tool-using MCP servers: run a fixed suite against a Model Context Protocol server, score the final answer, and enforce optional deterministic assertions over the tool trace. Extracted from [`intake-triage-mcp`](https://github.com/granolacowboy/intake-triage-mcp), and used to score that server's tools against golden expectations.

## What it does

- Connects to an MCP server over stdio, SSE, or streamable HTTP (see `connections.py`).
- Runs each question in a suite through a model (Claude) that must call the server's tools to answer.
- Captures the tool calls, timing, and the final answer.
- Scores answers with deterministic matchers: `exact`, `casefold`, `contains`, or `regex`.
- Can require or forbid tools, constrain tool order, assert selected tool arguments/results, and cap tool-call count.
- Can enforce per-task and per-tool latency budgets.
- Records ordered tool traces plus model, suite hash, harness revision, server revision, and timestamp.
- Writes Markdown, machine-readable JSON, and JUnit XML evidence.
- Can compare a run to prior JSON evidence and fail on accuracy or trace-validity regression.
- Tool inputs/results are omitted by default; redacted traces carry a SHA-256 of each tool result instead.

> **Engineering note:** [How I use AI agents to build deterministic systems without trusting the agents to be deterministic](https://granolacowboy.dev/writing/post-4-deterministic-ai) explains why this harness treats execution behavior as part of correctness.

## Why it exists

Tool-using systems fail in ways a unit test does not catch: the model picks the wrong tool, skips a required step, or answers without calling anything. Running a fixed suite through the real server turns those failures into a repeatable number, so a change to your server, tools, or prompt shows up as a drop in the score instead of a surprise in production.

## Layout

```
evals/
  evaluation.py     # the harness: load suite -> run each case -> score -> build report
  connections.py    # MCP transport setup (stdio / SSE / HTTP)
  requirements.txt  # anthropic + mcp
examples/
  intake-triage/    # a 10-pair golden suite (see examples/README.md)
```

## Quick start

```bash
pip install -e .
export ANTHROPIC_API_KEY=...          # the model that drives the tools

# stdio server, print the report
mcp-eval suite.xml -t stdio -c python -a my_server.py

# HTTP or SSE server, with a header
mcp-eval suite.xml -t http -u https://example.com/mcp -H "Authorization: Bearer TOKEN"

# save the report to a file instead of stdout
mcp-eval suite.xml -c python -a my_server.py -o report.md

# full option list
mcp-eval --help
```

Pass the suite file first. `-a/--args`, `-e/--env`, and `-H/--header` each accept one or more values, so a suite path placed directly after one of them is read as another value rather than as the positional argument.

Key options: `-t/--transport {stdio,sse,http}` (default `stdio`); `-c/--command`, `-a/--args`, `-e/--env` for stdio; `-u/--url`, `-H/--header` for SSE and HTTP; `-m/--model` (default `claude-sonnet-4-6`); `-o/--output` (default stdout). The suite file is the positional argument.

## Suite format

A suite is an XML file of `qa_pair` elements. Each pair is a question the model must answer using the server's tools, plus the exact answer it is scored against:

```xml
<evaluation>
  <qa_pair>
    <question>How many practice areas does the intake server list? Answer with just the number.</question>
    <answer>6</answer>
  </qa_pair>
</evaluation>
```

The model is prompted to return its final answer in `<response>` tags. Existing two-field suites remain valid. Optional constraints make the *path* testable as well as the answer:

```xml
<qa_pair>
  <question>Screen the prospective adverse party.</question>
  <answer match="exact">pending</answer>
  <required_tools><tool>intake_check_conflicts</tool></required_tools>
  <forbidden_tools><tool>intake_log_triage</tool></forbidden_tools>
  <tool_order><tool>intake_check_conflicts</tool></tool_order>
  <max_tool_calls>2</max_tool_calls>
  <max_duration_s>15</max_duration_s>
  <max_tool_duration_s>5</max_tool_duration_s>
  <required_calls>
    <call tool="intake_check_conflicts">
      <arguments>{"party_names":["Northgate Assurance Co"]}</arguments>
    </call>
  </required_calls>
  <result_assertions>
    <contains tool="intake_check_conflicts">pending</contains>
  </result_assertions>
</qa_pair>
```

A task passes only when the answer matcher succeeds and every declared trace constraint passes. `tool_order` is an ordered subsequence, so unrelated calls do not automatically invalidate the case. `required_calls` uses recursive JSON-subset matching, which lets a suite assert the consequential arguments without requiring incidental/default parameters to match exactly. `result_assertions` are evaluated against the raw tool result in memory, while the published trace remains redacted unless `--include-tool-results` is explicitly requested.

## Testing

The model-driven evaluation itself requires an Anthropic API key, but the harness also has offline unit tests for the deterministic parsing and input-normalization helpers. Those tests do **not** call a model or an MCP server.

```bash
pip install -r requirements-dev.txt
pytest -q
```

The central portfolio verifier runs the offline suite against `main` and verifies that the CLI imports and renders `--help` successfully. The verification job lives in [`granolacowboy.dev`](https://github.com/granolacowboy/granolacowboy.dev/actions/workflows/verify-intake-eval-harness.yml) so it can use the working self-hosted runner shared by this public portfolio.

## Report

The harness produces a Markdown report with provenance and evidence: model identifier, SHA-256 of the suite, harness revision, supplied server revision, UTC timestamp, answer/trace/overall scores, timing, ordered tool trace, and the model's summary and tool feedback.

Use `--server-revision <commit|tag|digest>` whenever the evaluated server has a stable identity. Use `--fail-under 100` only when you intentionally want the model-driven evaluation to become a gate.

For CI and regression analysis:

```bash
mcp-eval suite.xml -c python -a my_server.py \
  --server-revision "$(git rev-parse HEAD)" \
  -o report.md \
  --json-output evidence.json \
  --junit-output junit.xml

# On a later run, compare against a reviewed evidence file.
mcp-eval suite.xml -c python -a my_server.py \
  --baseline evidence.json --fail-on-regression
```

The baseline gate intentionally checks the aggregate pass percentage and trace-valid count. It does not pretend model latency or free-form feedback is perfectly stable across runs.

## CI

The central portfolio verifier validates the harness itself and stays offline. It deliberately does **not** spend model API credits.

A separate **Manual model-driven evaluation** workflow checks out `intake-triage-mcp`, runs the golden suite only when explicitly dispatched, appends the report to the GitHub job summary, and uploads the Markdown report as a retained artifact. It requires an `ANTHROPIC_API_KEY` repository secret and accepts an explicit pass threshold.

## Extending scoring

The deterministic scorer registry is the `SCORERS` mapping in `evals/evaluation.py`. Add a pure `(actual, expected) -> bool` function, register it by name, and add unit tests before exposing it in suite XML. This keeps ordinary evaluation explainable and reproducible. An LLM-as-judge scorer, if added later, should remain explicitly opt-in and should report its judge model and configuration as provenance rather than masquerading as a deterministic gate.

## Releases

The project is installable as `mcp-eval` through `pyproject.toml`. A `v*` tag must match the package version, runs the offline tests, builds wheel/sdist artifacts, and creates or updates the corresponding GitHub Release.

## Related

- **[intake-triage-mcp](https://github.com/granolacowboy/intake-triage-mcp):** the legal-intake MCP server this harness was extracted from and is used to score.
- **[llm-security-for-law-firms](https://github.com/MHSBai/llm-security-for-law-firms):** the security and adoption checklist that pairs with evaluation-driven delivery.

## Example

A runnable suite lives in [`examples/`](examples/): a 10-pair golden suite for `intake-triage-mcp` (conflict screening, matter validation, practice-area lookup, template drafting), each with an exact expected answer. Run it with the Quick start commands above, pointed at the suite file, then point the harness at your own server and suite to score it the same way.

## License

MIT. See [LICENSE](LICENSE).

---

<sub>Maintained by [Rich Berman](https://github.com/granolacowboy) / [MHSB Solutions](https://github.com/MHSBai). Contributions welcome.</sub>
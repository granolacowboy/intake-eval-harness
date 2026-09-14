# intake-eval-harness

A small evaluation harness for tool-using MCP servers: run a fixed suite of question/answer tasks against a Model Context Protocol server, let a model use the server's tools to answer, and score each final answer against a golden answer. Extracted from [`intake-triage-mcp`](https://github.com/granolacowboy/intake-triage-mcp), and used to score that server's tools against golden expectations.

## What it does

- Connects to an MCP server over stdio, SSE, or streamable HTTP (see `connections.py`).
- Runs each question in a suite through a model (Claude) that must call the server's tools to answer.
- Captures the tool calls, timing, and the final answer.
- Scores the final answer against the expected answer by exact string match.
- Writes a Markdown report (accuracy, per-task pass/fail, tool-call counts, timing) to stdout or a file.

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
pip install -r evals/requirements.txt
export ANTHROPIC_API_KEY=...          # the model that drives the tools

# stdio server, print the report
python evals/evaluation.py suite.xml -t stdio -c python -a my_server.py

# HTTP or SSE server, with a header
python evals/evaluation.py suite.xml -t http -u https://example.com/mcp -H "Authorization: Bearer TOKEN"

# save the report to a file instead of stdout
python evals/evaluation.py suite.xml -c python -a my_server.py -o report.md

# full option list
python evals/evaluation.py --help
```

Pass the suite file first. `-a/--args`, `-e/--env`, and `-H/--header` each accept one or more values, so a suite path placed directly after one of them is read as another value rather than as the positional argument.

Key options: `-t/--transport {stdio,sse,http}` (default `stdio`); `-c/--command`, `-a/--args`, `-e/--env` for stdio; `-u/--url`, `-H/--header` for SSE and HTTP; `-m/--model` (default `claude-3-7-sonnet-20250219`); `-o/--output` (default stdout). The suite file is the positional argument.

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

The model is prompted to return its final answer in `<response>` tags; that value is compared to `<answer>` by exact match.

## Report

The harness produces a Markdown report: a summary block (accuracy as correct/total and a percentage, average task duration, average and total tool calls) followed by one section per task (question, ground-truth answer, actual answer, pass/fail, duration, tool calls, and the model's own summary and tool feedback).

## CI

There is no bundled workflow, and the harness does not fail your build on a low score: it prints or writes a Markdown report and exits. Run it on demand, or wire it into your own CI or scheduled job and commit or archive the report as the record of a run.

## Related

- **[intake-triage-mcp](https://github.com/granolacowboy/intake-triage-mcp):** the legal-intake MCP server this harness was extracted from and is used to score.
- **[llm-security-for-law-firms](https://github.com/MHSBai/llm-security-for-law-firms):** the security and adoption checklist that pairs with evaluation-driven delivery.

## Example

A runnable suite lives in [`examples/`](examples/): a 10-pair golden suite for `intake-triage-mcp` (conflict screening, matter validation, practice-area lookup, template drafting), each with an exact expected answer. Run it with the Quick start commands above, pointed at the suite file, then point the harness at your own server and suite to score it the same way.

## License

MIT. See [LICENSE](LICENSE).

---

<sub>Maintained by [Rich Berman](https://github.com/granolacowboy) / [MHSB Solutions](https://github.com/MHSBai). Contributions welcome.</sub>
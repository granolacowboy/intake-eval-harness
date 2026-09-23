# Contributing

Contributions are welcome when they improve correctness, reproducibility, safety, or the usefulness of MCP evaluation.

## Development

```bash
python -m pip install -e ".[dev]"
pytest -q
mcp-eval --help
```

Do not put client, matter, credential, or other sensitive data in suites, fixtures, reports, issues, or pull requests.

## Adding evaluation behavior

A change to scoring or trace validation should include:

1. deterministic unit tests;
2. backwards compatibility for existing `<question>/<answer>` suites unless the change is intentionally breaking;
3. documentation of the new XML surface;
4. a clear explanation of whether the new rule affects the final answer, the tool trace, or both.

Prefer deterministic matchers and assertions. LLM-as-judge scoring may be added as an explicitly opt-in extension, but it should not silently replace deterministic gates.

## Adding a golden case

A useful case states a narrowly testable question and, where behavior matters, declares the expected tool contract:

```xml
<qa_pair>
  <question>...</question>
  <answer match="exact">...</answer>
  <required_tools><tool>example_lookup</tool></required_tools>
  <forbidden_tools><tool>example_write</tool></forbidden_tools>
  <max_tool_calls>2</max_tool_calls>
</qa_pair>
```

If ordering is itself an invariant, add `<tool_order>` with the required subsequence.

## Pull requests

Keep PRs focused. Run the offline suite before opening one. Model-driven runs are intentionally manual because they incur external API use and can vary with model behavior.

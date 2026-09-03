# Example — evaluating intake-triage-mcp

`intake-triage/evaluation.xml` is the 10-pair golden suite for the deterministic
legal-intake MCP server this harness was extracted from
([intake-triage-mcp](https://github.com/granolacowboy/intake-triage-mcp)) — conflict
screening, matter validation, and practice-area lookup, each with an exact expected answer.

## Run it

```bash
pip install -r ../evals/requirements.txt
export ANTHROPIC_API_KEY=...            # the judge/agent model
python ../evals/evaluation.py \
    --server "python -m intake_triage_mcp" \
    --suite intake-triage/evaluation.xml \
    -o report.md
```

Each question is run through a model that must use the server's tools; the answer is
scored against the golden `<answer>`, and a JUnit-style `report.md` is written (accuracy,
per-tool pass counts, model, timestamp). Commit `report.md` next to this file to publish results.

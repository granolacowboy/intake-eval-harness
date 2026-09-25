# Evaluation evidence schema

The JSON evidence file is the durable machine-readable output of a model-driven run.

Top-level fields:

- `schema_version`: evidence format version.
- `provenance`: exact model, suite SHA-256, harness revision, server revision, UTC timestamp, and run label.
- `summary`: aggregate pass counts, accuracy, trace-valid count, duration, and tool-call totals.
- `results`: one record per task, including expected/actual answer, matcher, trace violations, duration, and the redacted/public tool trace.
- `baseline_comparison`: present when `--baseline` is used.

By default, tool arguments are omitted and tool results are represented by SHA-256 rather than raw content. Use `--include-tool-inputs` or `--include-tool-results` only when the evidence destination is appropriate for that data.

## Extended suite assertions

A case can declare deterministic execution constraints in addition to the answer:

~~~xml
<qa_pair>
  <question>...</question>
  <answer match="exact">refused</answer>

  <required_tools>
    <tool>intake_log_triage</tool>
  </required_tools>

  <required_calls>
    <call tool="intake_log_triage">
      <arguments>{"matter_name":"Unsafe Write Test","conflicts_status":"not-run"}</arguments>
    </call>
  </required_calls>

  <result_assertions>
    <contains tool="intake_log_triage">conflicts gate</contains>
  </result_assertions>

  <max_tool_calls>2</max_tool_calls>
  <max_duration_s>15</max_duration_s>
  <max_tool_duration_s>5</max_tool_duration_s>
</qa_pair>
~~~

`required_calls` performs recursive JSON-subset matching. A case can therefore assert consequential arguments without coupling itself to incidental/default fields.

`result_assertions` inspect raw tool results during evaluation even when those results are redacted from the published evidence.

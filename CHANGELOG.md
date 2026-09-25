# Changelog

## Unreleased

### Added
- JSON evidence output with provenance and redacted result hashes;
- JUnit XML output for CI systems;
- recursive required-call argument assertions;
- tool-result substring assertions;
- per-task and per-tool latency budgets;
- baseline comparison and a fail-on-regression gate;
- issue forms for evaluator bugs and evaluation-case proposals.

### Changed
- the manual model-driven workflow now retains Markdown, JSON, and JUnit evidence together.

## 0.1.0

Initial reusable MCP evaluation harness with deterministic answer matchers, required/forbidden tools, ordering constraints, call-count limits, provenance, and manual model-driven CI.

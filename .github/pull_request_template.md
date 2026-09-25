# Change summary

Describe the evaluation behavior or developer-experience change.

## Evaluation contract

Which layer changes?

- [ ] suite parsing
- [ ] deterministic answer scoring
- [ ] trace assertions
- [ ] evidence/reporting
- [ ] baseline/regression policy
- [ ] transport
- [ ] packaging/release

## Determinism and privacy

Explain whether the change requires retaining or publishing additional tool inputs/results. LLM-as-judge behavior must remain explicitly opt-in and provenance-bearing.

## Evidence

- [ ] Offline unit tests added/updated.
- [ ] Existing two-field question/answer suites remain valid, or a breaking change is explicitly justified.
- [ ] README/schema docs updated.
- [ ] No credentials or private evaluation payloads are included.

## Compatibility

Describe XML, CLI, JSON evidence, or JUnit compatibility impact.

# Phase 5P: Mapping-Only Task Taxonomy Re-Audit

`ros4hc_synthetic_scenario.mapping_only_audit` reuses the Phase 5O
`generated_questions.jsonl` and `router_results.jsonl` without generation,
routing, retrieval, or taxonomy changes. It validates 48 unique matching IDs,
question text equality, saved TaskSpec shape and hashes both source files.
Only question ID, user request and saved TaskSpec enter auditor batches.
The old audit and router context are excluded. Current allowlists, schema and
parser instructions supply definitions; no independent semantic glossary exists.

Four batches of 12 use `gpt-6-astra`, medium reasoning, strict JSON schema,
`store=false`, no tools, web or temperature. Calls are sequential with no automatic
retry; the hard cap is six. Failed runs preserve their manifest and successful
batch results. A nonempty output directory is rejected to avoid overwriting or
accidentally repeating a run. Credentials stay in the existing provider transport
and are never serialized; errors persist only exception type.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ros4hc_observation/ros4hc_synthetic_scenario \
python3 -m ros4hc_synthetic_scenario.mapping_only_audit \
  --source ~/ros4hc_experiments/phase5o/taxonomy_stress_test_20261108 \
  --output /tmp/phase5p-fixture --dry-run
```

For the authorized live audit, first source `~/.config/ros4hc/openai.env`,
remove `--dry-run`, and use the designated Git-external Phase 5P directory.
Do not print or save the key. Fixture results are wiring checks, not semantic
judgments. Run focused pytest, package pytest, isolated colcon build/test,
`git diff --check`, source audit and fixture before live execution.

The four mapping labels exclude `unsupported_capability`. Deterministic Python
aggregation reports status proportions, per-task rates, research_planning details,
missing dimensions, consolidated candidate frequencies and a maximum 20-row review
queue. Explicit versioned aliases consolidate known synonyms; each candidate counts
unique supporting question IDs. One supporting question is weak; repeated support
is stronger, not validated evidence for changing taxonomy. Queue priority weights:
forced/unmapped 8, candidate 4, unnatural research_planning 2, human-review flag 2,
ambiguous 1; ties sort by question ID. Human reviewer fields remain empty.

これはLLM生成質問とLLM Auditorによるpilotであり、正式なrouting accuracy評価ではない。
Taxonomy revision requires human review and further discussion. No commit or push
is part of this experiment.

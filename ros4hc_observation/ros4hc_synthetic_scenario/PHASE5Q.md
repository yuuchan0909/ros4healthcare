# Phase 5Q Boundary / Multi-intent Task Taxonomy Stress Test

Purpose: explore difficult boundaries of the existing single-task representation, rather than estimate routing accuracy. Phase 5P re-audited ordinary blind questions; this experiment deliberately targets boundary and compound requests.

The generator receives only natural-language boundary themes, domain/style guidance, and an ID/batch/theme schedule. It never receives production task/information/connector lists, prior questions, or candidate task names. Ten primary themes receive 4–5 questions each, in four independent calls of 12 questions. Theme overlap is allowed. Exact normalized duplicates are rejected; semantic duplication still needs human review.

The unchanged production `task_router.make_request`, `parse_task`, and `validate_task` process each question. No context building, retrieval, connector execution, grounded answers, or knowledge acquisition occurs. The mapping-only auditor receives the original request, saved TaskSpec, and current definitions. Multiple intents can be well mapped through requested outputs and required information. Temporal and information meaning are audited independently, with extraction mistakes distinguished from vocabulary and task representation limitations.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=ros4hc_observation/ros4hc_synthetic_scenario \
  python3 -m ros4hc_synthetic_scenario.boundary_taxonomy_stress_test \
  --dry-run --output /tmp/phase5q-fixture
```

After offline validation, source `~/.config/ros4hc/openai.env` without printing it and run the same command without `--dry-run`, using the Git-external output directory:
`~/ros4hc_experiments/phase5q/boundary_taxonomy_stress_test_20261110/`.

Live configuration: gpt-6-astra, medium reasoning, store=false, no tools/web/temperature. Planned calls: generator 4, router 48, auditor 4; hard total cap 60. Only transient router errors receive at most one retry per question. Failure manifests and partial results remain saved; a failed directory must not be overwritten or blindly rerun. API keys and provider error bodies are never persisted.

Candidate aliases are explicit and deterministic. Task candidates require forced/unmapped support. Weak means one question, emerging 2–3, strong at least 4; themes and domains accompany counts. Counts are exploratory, not a basis for automatic expansion. Review queue is capped at 20, with blank reviewer fields. Task, information, multi-intent, temporal, and overload analyses are saved separately. A/B/C/D is a provisional heuristic, subject to human review.

これは意図的に難しい質問を生成したstress testであり、自然な研究質問全体の分布を表すものではない。
正式なrouting accuracy評価ではない。

Production taxonomy, Router, connectors, knowledge and observation code are unchanged. Phase 5J files and apple_watch_replay are excluded. Do not commit or push as part of this experiment.

Live run completed: 48 questions, 48 successful router outputs, 56 calls (4 generator + 48 router + 4 auditor), no retries. Mapping-only audit: 45 well_mapped, 3 forced_mapping, no unmapped/ambiguous. Q001/Q011/Q031 support one emerging candidate, `inference_appraisal`; the observed auditor synonym `research_inference_evaluation` was consolidated only after generation, without changing raw results or adding calls. No new information candidates. All five designated multi-intent questions were well represented; Q044 had a minor temporal extraction omission. Provisional judgment B requires human review; no taxonomy revision is recommended yet. Similar generated situations and near-boundary audit variation limit the interpretation.

Validation: focused pytest 15 passed; package pytest 726 passed; isolated colcon build/test succeeded (726 tests, zero errors/failures). All 13 requested artifacts are Git-external; see `report_ja.md` and `final_audit.json` in the output directory above. Protected production files and HEAD were unchanged; no commit/push.

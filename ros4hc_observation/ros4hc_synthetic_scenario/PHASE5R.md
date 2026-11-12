# Phase 5R — Machine Knowledge Human Evaluation Pilot

## Purpose

Phase 5R evaluates the reliability of Phase 5M machine-extracted Knowledge before treating it as a dependable input to downstream retrieval or grounded answering.

The pilot deliberately separates two questions:

1. **Knowledge → source:** Is an extracted Knowledge record faithful to its cited source paragraph(s)?
2. **Source → Knowledge:** Does the extraction recover important Knowledge that is present in the source text?

This prevents a high apparent precision from hiding severe omissions.

## Scope

This is an **evaluation pilot**. The completed pilot includes human + ChatGPT-assisted blind annotation, an AI preliminary audit, and human + ChatGPT-assisted spot checking; it is not an independent human-only evaluation. It does not verify the source paper scientifically or establish clinical validity.

The Phase 5M artifacts remain read-only. The evaluator must not change `machine_knowledge.jsonl`, `source_segments.jsonl`, or the approved source XML.

## Blind review order

To reduce confirmation bias, review in this order:

1. `coverage_blind.csv`
2. Save/freeze the completed blind annotations.
3. Only then open `knowledge_review.csv`.

`coverage_blind.csv` intentionally contains no machine Knowledge output.

## Pilot A — Knowledge faithfulness

Thirty Knowledge records are selected deterministically with a fixed seed and stratified quotas:

| Knowledge type | n |
| --- | ---: |
| experimental_method | 8 |
| measurement_method | 4 |
| analysis_method | 5 |
| empirical_finding | 5 |
| limitation | 4 |
| study_design | 2 |
| review_statement | 1 |
| other | 1 |
| **Total** | **30** |

Review label schema (the completed pilot below reports only the assessed fields):

- **faithfulness**
  - `Supported`: the source alone directly supports the extracted Knowledge.
  - `Partially supported`: the core idea is present, but a condition, scope, or qualification is lost or mildly generalized.
  - `Unsupported`: the source does not support the claim, or unsupported content was added.
- **type_correct**: `Yes` / `No`
- **qualification_preservation**: `None` / `Minor` / `Major`
- **hallucination**: `No` / `Yes`
- **semantic_provenance**: `Yes` / `Partial` / `No`

Structural provenance is not re-scored by the reviewer: record IDs, paragraph IDs, section metadata, and hashes are already validated by code. Human review addresses whether the cited text *semantically* supports the Knowledge.

## Pilot B — Blind coverage

Twenty substantive body passages are sampled deterministically. The target category allocation is:

| Source category | n |
| --- | ---: |
| methods | 8 |
| results | 6 |
| discussion_limitations | 4 |
| other | 2 |
| **Total** | **20** |

Pilot eligibility is `role=body` and source text length ≥ 80 characters. If a stratum is too small, the deficit is deterministically reallocated from the remaining eligible passages.

Without viewing machine output, the reviewer records:

- `gold_extractable`: whether the passage contains at least one useful structured Knowledge item.
- `gold_knowledge_types`: expected Knowledge type(s), if separately annotated. No Knowledge-type labels were inferred or added in the completed pilot.
- `gold_knowledge_items`: concise reviewer-authored Knowledge items, preserving conditions and scope. The completed pilot used human + ChatGPT-assisted blind annotation.

A later comparison stage should map these Gold items to machine Knowledge to estimate coverage/recall. That semantic mapping is intentionally not automated in this preparation script.

## Interpretation

Do **not** define an arbitrary pass threshold before the pilot. Use the pilot diagnostically:

- frequent `Unsupported` records → extraction prompt/schema needs revision;
- repeated `Major` qualification loss → conditions/scope representation needs revision;
- strong faithfulness but poor coverage → extraction is overly conservative or chunking/schema misses important information;
- weakness concentrated in one Knowledge type → type definition or extraction instructions need targeted revision.

The extraction method should be frozen before a larger final evaluation is reported.

## Outputs

The preparation script creates:

- `coverage_blind.csv`
- `knowledge_review.csv`
- `sampling_manifest.json`
- `EVALUATION_GUIDE.md`

`sampling_manifest.json` records the source audit, seed, quotas, observed sample counts, and SHA-256 hashes of generated review artifacts.


## Completed Phase 5R pilot

### Coverage pilot

- Blind Gold作成対象は **C001〜C010の10段落**、Gold Knowledgeは **26項目**。準備されたC001〜C020のうち、今回完了した範囲はC001〜C010のみである。
- 10段落すべてについて、対応するmachine-extracted Knowledgeが確認された。
- 今回の手作業照合範囲では、26 Gold項目すべてに対応内容がmachine Knowledge側で確認された。
- これは限定サンプルでのpilot上の確認であり、最終的なrecall=100%や一般化性能を主張しない。
- Gold作成は **human + ChatGPT-assisted blind annotation** であり、完全な独立human-only evaluationではない。
- **C001/C002には評価手順確立前の補助が入った可能性があるため、最終評価用Goldとは扱わない。**
- Knowledge type等の追加ラベルは推測・補完していない。

### Faithfulness pilot

`knowledge_review.csv`の30件を対象に、それぞれのprovenanceの`source_text`のみを根拠としてAI一次監査を行った。判定は抽出内容のsource_textへのfaithfulnessに限定し、他の段落や外部知識による補完は行っていない。

| Preliminary label | 件数 |
| --- | ---: |
| Supported | 30 |
| Partially supported | 0 |
| Unsupported | 0 |
| **Total** | **30** |

これは **AIによる一次監査であり、最終human Goldではない**。この30件の結果を、独立した人間評価で確定した抽出精度として扱わない。

### High-priority spot check

以下の5件は、表・数式のテキスト化による読み取り不確実性のため追加確認した。

| Sample ID | Knowledge ID | 主な確認箇所 |
| --- | --- | --- |
| K003 | knowledge_0094 | Pitch列、比較対象、p値の対応 |
| K006 | knowledge_0052 | 総和範囲、8関節、絶対値の適用範囲 |
| K008 | knowledge_0083 | Ramp行、指標列、各数値の対応 |
| K010 | knowledge_0055 | 平均係数、総和範囲、分散項の構造 |
| K012 | knowledge_0050 | 指数型報酬の数式構造、スケール値と単位 |

5件ともsource_textとmachine Knowledgeの内容に明確な矛盾は確認されず、Supportedと判断した。ただし、このspot checkも **human + ChatGPT-assisted review** であり、独立評価者によるblind human evaluationとは扱わない。30件全体の最終human Goldが確定したことを意味しない。

### Pilot artifacts

- Review inputs: `/home/bushi/ros4hc_experiments/phase5r/knowledge_human_eval_pilot_20261112/`
- Frozen pilot Gold: `/mnt/c/Users/bushi/phase5r_pilot_gold_C001_C010.json`
- AI preliminary audit: `/mnt/c/Users/bushi/phase5r_machine_faithfulness_pilot.md` and `.json`

The AI audit artifacts retain the preliminary audit results; the five-record assisted spot-check outcome is documented separately above. These artifacts are pilot records, not final independent human evaluation labels.

### Phase 5R pilotとしての結論

「本pilotでは、machine Knowledge extractionについて明確な取りこぼしやunsupported extractionは確認されなかった。ただし、対象は1論文・限定サンプルであり、AI-assisted annotation/auditを含むため、抽出精度100%や一般化可能性を示すものではない。」

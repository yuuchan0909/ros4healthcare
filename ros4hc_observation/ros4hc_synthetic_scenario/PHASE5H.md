# Phase 5H: conservative condition-aware retrieval baseline

目的は単語一致の候補と、明示された測定条件の対応がある候補を区別すること。既存の3 papers / 6 chunksだけを使う工学的baselineであり、医学的妥当性、診断、個人の転倒リスク、研究品質を判定しない。verifiedは出典確認状態であってapplicableの意味ではない。

`condition_aware_retrieval.py`に独立した`ConditionAwareRetrievalBaseline`を追加した。Phase 5F LexicalRetrievalBaseline、generate_query、既存build_bundleは変更しない。新クラスのbuild_bundleは独自methodと全件auditを記録する。

候補生成とstructured applicability確認の2段階。候補は既存lexical signalまたはstructured modality対応の和集合。最終evidenceは少なくとも1つのmodality対応があるcompatible/partialだけ。mismatchとunknownはauditには残すがevidenceには入れない。欠測を否定的な医学的判断に読み替えない。

## Applicability契約

- measurement: supported_query_modalities / unsupported_query_modalities / matched_variables / unmatched_variables / measurement_status / measurement_reasons / record_conditions。
- population: population_status / reason / record_conditions。
- fall_outcome: fall_outcome_status / record_conditions / field_present。fall_definition、prospective_or_retrospective、followup_duration、ascertainment_methodの値と既知状態を保持。
- overall_status: compatible / partial / mismatch / unknown。
- unknown_conditions: 未確認のpopulation、clinical_condition、protocol/test_name/body_locationの同等性、欠測の測定・outcome項目。

measurementはmatch / partial / mismatch / unknown。casefoldと空白正規化後の変数完全一致だけを使い、activityをgait speedへ変換しない。modalityに限りHR、activity/inertial、visualの小さな明示familyを使う（一覧はmoduleのFAMILIES）。未登録modalityは同じ表記の場合のみ対応。unknown/not_reported等は対応根拠にしない。

queryとrecordのmodalityが既知で対応がゼロならmismatch。一部modalityのみ対応、変数不一致、変数未確認ならpartial。全modalityと全変数が対応すればmatch。queryまたはrecordのmodality不明ならunknown。protocol、test_name、body_locationは構造化metadataから保持するが自由文で同等性を判定しない。matchもこれらの条件を検証済みという意味ではない。

overallはmeasurementのmatchをcompatibleへ写し、他は同名にする。populationのunknownだけで排除しない。これは測定情報の対応に限るラベルでありclinical applicabilityではない。

Phase 5B Sensor Factsには年齢・性別・BMI・frailty・疾患・medication・転倒歴がないためgenerate_queryのpopulationとclinical_conditionはNone。population_statusはunknown。将来の構造化person contextのため群を分けたが、現在はquery populationが渡されても自動matchingは実装せずunknownを保つ。

fall_outcomeは転倒定義、把握時制、把握方法が明示されればdocumented（追跡期間の欠測は別記）。Jiaはretrospectiveを保持。reviewのheterogeneous/not_reportedはunknown。Doの既存2つのascertainment_methodの明示的な「測定なし」宣言に限り完全一致でno_fall_outcomeとする。他の自由文から自動否定判定しない。この限定規則は新しい記載形式には人手で拡張が必要。品質点数を作らずprospectiveをretrospectiveより上位にしない。

## 順位と出力

昇順tuple: overall class（compatible=0, partial=1, unknown=2, mismatch=3）、supported modality数の負値、matched variable数の負値、既存lexical signalの負値、paper_id、chunk_id。入力順によらず決定的。lexical signalはPhase 5Fの工学的数値をそのまま使い、clinical evidence scoreを作らない。

結果はapplicability、ranking_key、lexical_signalに加え原文evidence_text、DOI、paper/chunk ID、provenance.curation.fall_risk、元record SHA-256を保持。threshold/AUC/ORは保存するだけで適合性や順位に使わない。

## 実corpusのcontrolled audit

`condition_aware_evaluation.py`のA–Cは既存generate_query、D–Fは直接EvidenceQueryを使う。visual用にSensor Factsを変更しない。

| case | 最終evidence | 意味 |
|---|---|---|
| A HR only | 0 | 現corpusのHR evidence coverage gap |
| B activity | Jia 2 + review 2、すべてpartial | inertial familyで候補化、activity変数の直接一致なし |
| C HR + activity | Jia 2 + review 2、すべてpartial | HRはunsupported、Do 2はmismatch |
| D gait speed | Jia gait-speed chunkが先頭、compatible | gait speedの完全一致 |
| E TUG time | Jia TUG chunkが先頭、compatible | Timed Up and Go timeの完全一致 |
| F home images / environmental fall hazards | Do 2、hazard chunkが先頭でcompatible | contextに応じた画像測定対応 |

Do論文はmultimodalという単語でlexical候補になってもHR/ACC measurement mismatchなので最終evidenceへ入らない。画像queryでは対応する。論文自体を一律除外していない。

再現例（packageをPYTHONPATHへ設定するかisolated installをsource）:

```bash
python3 -m ros4hc_synthetic_scenario.condition_aware_evaluation --papers literature/real/papers.jsonl --evidence literature/real/evidence_chunks.jsonl --output /tmp/ros4hc_phase5h_audit
```

verified-only変換、manifest/coverageとcontrolled_retrieval.jsonl（全件auditを含む）をGit外へ保存する。既存registryは変更しない。

## 制限と次段階

3論文だけのcontrolled engineering auditであり、検索性能や臨床的適用性の一般化はできない。HR evidenceは現corpusにない。inertial対応はprotocolや身体装着位置の同等性、自由生活activityと標準歩行試験の互換性を示さない。reviewは単一集団・protocol・outcomeを表さない。環境hazardとfall-event outcomeは別である。

今後は構造化person context、明示的protocol条件比較、別途評価したsemantic retrievalを検討する。Sensor-only vs Sensor+Evidenceの引用整合性・unsupported inference・情報不足時の応答評価は未実施。今回LLM/API、embedding、vector DB、web retrieval、転倒確率予測、World Model、production Observation変更は含まない。

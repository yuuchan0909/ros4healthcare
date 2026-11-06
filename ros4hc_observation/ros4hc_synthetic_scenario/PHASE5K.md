# Phase 5K: Generic Task Router & Knowledge Connector MVP

Generic task-oriented research platform。fall is a current case study only。
LLM converts language -> structured task。router is not a free agent。
connector selection is deterministic、connectors are allowlisted。
missing knowledge remains missing。no diagnostic autonomy、no general medical validity claim。
最終LLM回答・診断・個人の確率算出・行動実行は実装しない。
Agentic RAG、web、vector DB、GraphRAG、World Modelは導入しない。
既存Phase 5H/I/J、production Observation、real corpusは変更しない。

## Language boundary

`task_router.py`は自然言語文字列だけをmodelへ渡し、Sensor Factsや管理envelopeは送らない。
TaskSpecはstrict JSON Schema、全field required、additionalProperties=false。
fields: task_type / target_concepts / requested_outputs / temporal_scope / required_information /
requires_sensor_history / requires_scholarly_evidence / requires_experimental_methods /
requires_sensor_capabilities / requires_action_policy / ambiguities。
booleanとrequired_informationの整合性、enum、重複、JSON重複keyをローカルで再検証する。
不正TaskSpecはroutingせずinvalid。unknownは情報要求を空にしてclarificationを残す。
自由文字列のtargetは実行名に変換せずdataとしてのみ扱う。

対応task_typeは10個に固定：human_state_interpretation、future_risk_hypothesis、
scholarly_evidence_lookup、research_planning、experiment_planning、experiment_automation、
sensor_selection、additional_measurement_planning、intervention_planning、unknown。

## Deterministic registry

| required_information | connector | 実装状態 |
|---|---|---|
|current_observation / sensor_history|SensorHistoryConnector|明示的に供給されたPhase 5B測定入力のみ|
|scholarly_evidence|ScholarlyEvidenceConnector|限定的な現corpus adapter|
|experimental_methods / measurement_methods / analysis_methods|ExperimentalMethodConnector|unavailable / not_implemented|
|sensor_capabilities|SensorCapabilityConnector|unavailable / not_implemented|
|action_policy|ActionPolicyConnector|unavailable / not_implemented|

connector名をLLMに選ばせず、固定mappingから固定順・重複なしで選ぶ。
Sensor FactsはPhase 5B再帰allowlistで検査し、sample_id / ground truth / hidden activity labelを含む入力は拒否する。
history要求に履歴が供給されなければunavailable。自動的に患者データを探さない。
将来のexperimental methods、sensor capabilities、intervention policiesは別途curationが必要。

## Generic request and narrow fall adapter

generic KnowledgeRequestはtarget_concepts / task_type / measurements / population / outcomes / knowledge_types。
measurementsは実際に供給されたSensor Factsからのみ抽出、population=null、outcomes=[]（推測しない）。
現在のKnowledge Base coverageはfall-related 3 papers / 6 verified chunksのみ。

adapterはTaskSpecの全targetが明示的fall aliasで、ユーザー要求全体が手書きのfall-only検索文法に一致した場合のみ起動する。
例「転倒に関連する研究を調べたい」、英語「find research on falls」。
未知・疾患・混合domain・広い要求・表現上の不確実性はすべてcoverage gapにする。
Parkinson要求をLLMが誤ってfallsへ置換しても、生の要求文側のguardが拒否する。
要求概念をLLMが落とす問題を一般に解決したわけではない。保守的な有限文法で対応範囲を制限する。
この制限はadapterに閉じ、core routerをfall-specificにはしない。
corpus側も既存curated paper ID / DOI allowlistとfall titleを検査し、target/provenance mismatchを拒否する。

測定情報あり：Phase 5H ConditionAwareRetrievalBaselineのfinal Evidenceだけを取得。
audit-only / mismatchをcontextへ出さず、measurement coverage gapを残す。partialは個人への直接適用ではない。
測定情報なし：既存LexicalRetrievalBaselineでfall topic lookupのみ。
測定モダリティを捏造せず、全件applicability unknown、evidence_applicability_unassessed gapを明示する。
これはPhase 5Hの測定対応が成立したfinal Evidenceと同等ではない。
どちらも小規模なlocal registryだけで、網羅的学術検索でも疾患知識でもない。

## Context and missing knowledge

Final Reasoning Contextはuser_request / task_spec / connector_results / coverage_gaps / unavailable_sources。
unavailableはstatus / reason / missing_information / data=nullを返す。
未実装connector、未供給history、対象domainなしを成功した知識取得と見せない。
最終回答LLMには接続せず、contextの生成と監査までを今回のMVPとする。

## Execution

```bash
export PYTHONPATH="$PWD/ros4hc_observation/ros4hc_synthetic_scenario${PYTHONPATH:+:$PYTHONPATH}"
PYTHONDONTWRITEBYTECODE=1 python3 -m ros4hc_synthetic_scenario.task_router \
  --dry-run --output /tmp/ros4hc_phase5k_dry_20261106 \
  --sensor-input ~/ros4hc_experiments/inputs/phase5b_clean_a100fd0/condition_c_structured.jsonl
source ~/.config/ros4hc/openai.env
PYTHONDONTWRITEBYTECODE=1 python3 -m ros4hc_synthetic_scenario.task_router \
  --output ~/ros4hc_experiments/phase5k/astra_router_smoke_20261106 \
  --sensor-input ~/ros4hc_experiments/inputs/phase5b_clean_a100fd0/condition_c_structured.jsonl
```

4 controlled cases固定。dry-runは手書きTaskSpec fixturesを使いprovider calls=0。
これはoffline自然言語分類ではなく、期待TaskSpecからのschema/routing/connector検証である。
Sensor Facts指定時はPhase 5Iの選択関数で最初の完全履歴recordを取得し、task_input以外をモデル/contextへ送らない。
指定しない場合Case Aは明示的unavailable、Case Dはtopic lookupとして扱う。

liveは4 cases / 4 calls上限、再試行・内容改善の再生成なし。
gpt-6-astra / reasoning_effort=medium / store=false、temperature/max_output_tokens/toolsなし、webなし。
API commandごとにenvをsourceし、key値・headers・例外本文は保存しない。
results.json / manifest.json / report_ja.mdをGit外・空の出力先へ保存する。
各caseのTaskSpec/context、request/payload、prompt/input SHA256、usage、commit、UTC時刻を記録する。
失敗もcheckpointし、残りのcaseを続行する。LLMの分類誤りを自動修正しない。

## Controlled examples

|Case|要求|期待route|
|---|---|---|
|A|この人の最近の状態変化を見たい|human_state_interpretation → sensor_history|
|B|パーキンソン病の研究に必要なセンサーは？|research_planning又はsensor_selection → scholarly_evidence / measurement_methods / sensor_capabilities。不足を明示|
|C|歩行評価実験を自動化したい|experiment_automation → experimental_methods / sensor_capabilities / analysis_methods。不足を明示|
|D|転倒に関連する研究を調べたい|scholarly_evidence_lookup → scholarly_evidence。限定的な既存fall corpusのみ|

4例のsmokeから分類精度・医学的妥当性・汎用的domain対応能力を主張しない。

## 2026-11-06 pre-live validation

HEAD=897aa4a / human-state-interpretation。
focused pytest 30 passed、package pytest 565 passed、isolated colcon build成功、
colcon test 565 tests / 0 errors / 0 failures / 0 skipped。
git diff --checkと新規4ファイルの空白検査が成功。
Phase 5H/Iとprovider/corpusはHEADから変更なし。Phase 5Jの既存3ファイルも前回SHA256と一致。

dry-run `/tmp/ros4hc_phase5k_dry_20261106` は4 cases / 0 API calls。
Aは明示的に供給された合成Sensor Factsのhistoryへ接続。
Bはscholarly_evidence / measurement_methods / sensor_capabilitiesがunavailable。
Cはexperimental_methods / analysis_methods / sensor_capabilitiesがunavailable。
DはPhase 5H final Evidence 4 chunks / 2 papers、coverage gap=measurement:hr。
Parkinson・混合domain・LLMの誤ったfalls target・取得corpusのtarget不一致を拒否するtestが成功。

## 2026-11-06 live router smoke

`~/ros4hc_experiments/phase5k/astra_router_smoke_20261106`へ保存。
gpt-6-astra / medium、4 cases / 4 calls、全件strict TaskSpec検証成功。再試行なし。
usage: input 2308 / output 979 / total 3287 tokens。
A=human_state_interpretation、B=sensor_selection、C=experiment_automation、D=scholarly_evidence_lookup。
4例とも期待するrequired_informationを満たし、TaskSpecからの固定routeとcontextが一致した。
Bの全sourceはunavailableで、Parkinson evidenceとしてfall literatureを返していない。
Dのみfall corpusを取得し、4 chunks / 2 papers、all partial、measurement:hr gapを維持した。
request/payloadのstore=false / medium / tools等なし、usage合計も再監査した。
4例のlanguage-to-TaskSpec接続確認であり、汎用分類性能・医学的妥当性の評価ではない。
最終LLM回答・自律的tool実行・診断・介入は行っていない。commit/pushなし。

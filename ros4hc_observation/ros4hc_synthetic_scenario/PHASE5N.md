# Phase 5N: Generic Machine Knowledge Connector & Grounded End-to-End MVP

1論文・少数taskによる研究用MVP。machine-extracted Knowledgeはhuman verification未実施。
汎用研究支援の完成、全domain対応、科学的正確性、retrieval accuracy、臨床判断を主張しない。

## Architecture

User Request → Phase 5K LLM TaskSpec parser → deterministic registry → read-only
MachineKnowledgeAdapter → bounded provenance context → final explanation LLM → strict
schema and citation validation. LLMの役割はTaskSpec生成と最終説明のみ。

`build_context(..., machine_adapter=adapter)`は明示的opt-in。既存5K呼出しはそのまま使える。
ExperimentalMethodConnectorはexperimental_methods → experimental_method/study_design、
measurement_methods → measurement_method、analysis_methods → analysis_methodを検索。
ScholarlyEvidenceConnectorは既存FallCorpusAdapterの厳格な文法を先に判定し、
それ以外でのみmachine adapterを使用する。source_categoryは
curated_verified_evidence / machine_extracted_knowledgeを分離する。
既存fall corpusの変換・書換えや、machine Knowledgeのverified Evidence化は行わない。

## Input and retrieval

Phase 5Mのsource_document.json、source_segments.jsonl、machine_knowledge.jsonlをread-onlyで読む。
DOI/PMCID、元XML SHA256、XMLから再構成した全段落、各record schema/provenance、pending、
machine_extracted、human_verified=falseを検証。実行後も3成果物と元XMLのhashを照合する。
元XMLはPhase 5M documentに記載された既存ローカルファイルのみ。取得通信は行わない。

DOI/PMCIDがあれば全明示IDのexact matchを要求。それ以外はgeneric stopwordを除き、
日本語alias（四足→quadruped等）を含むlexical overlapを使う。quadruped/cpg/multiagent/
locomotion/hopfのうち2つ以上のanchorを要求。これは限定的な保守的ルールであり全domain分類ではない。
Parkinson/fall/cardiovascular等の混在要求はidentifier未指定時に拒否する。

要求されたknowledge_typeでfilterし、target concept overlap、extracted field keyword overlap、
knowledge_idの順で安定ranking。要求情報groupごとに最良候補を1件確保したうえで上位候補を追加する。
最大8件。複数connector時も実験方法経路を優先して最終contextを8 unique recordsに制限する。
semantic duplicate merge、embedding、vector DBは使わない。

## Final boundary

answer_ja、supported_points、unknown_or_unsupported、limitations、additional_information_neededの
strict JSON。全object additionalProperties=false。supported_pointsのverification_statusは
machine_extracted_pendingのみ。knowledge_id、paragraph_idは取得集合・元record・段落関係・hashと再照合。
本文内の架空/未宣言引用もrejectする。意味的entailmentの完全保証はしない。

machine contextがある回答には
「以下は機械抽出Knowledgeに基づき、人手による学術的検証は未実施」を必須とする。
fall curated contextは検索経路・件数・適用性未評価のみ最終説明し、このmachine専用citation schemaでは
fall論文の科学的内容を要約しない。fall retrieval自体は既存pathを保持する。

## Controlled execution

```bash
export PYTHONPATH=ros4hc_observation/ros4hc_synthetic_scenario
python3 -m ros4hc_synthetic_scenario.grounded_research_assistant \
  --dry-run --output /tmp/phase5n-dry
```

Dry-runはauthored TaskSpecとfixture final responses。API/network calls=0。
`--dry-run`なしはlive。`--validation FILE`に全7検証の成功、code hashes、source auditが必要。
検証順はfocused pytest、package pytest、isolated colcon build/test、diff check、dry-run、source audit。
validation記録は実行者がチェック結果とともに作成する。これは署名済み認証機構ではない。

Live出力先:
`~/ros4hc_experiments/phase5n/generic_e2e_mvp_20261106/`

Router最大4回、Final最大4回、retryも含め全体最大8回。transient errorのみ1 retry可。
再生成・内容修正目的のretryは禁止。予算不足時は停止。各attempt前にopenai.envをsourceし、
keyを表示/保存しない。gpt-6-astra、medium、store=false、temperature/tools/webなし。
manifestには条件別attempt/usage、source/code hashes、offline gateを保存する。
report_ja.mdは各caseのTaskSpec、routing、context、回答全文、引用検証、不足を収録する。
新規paper取得・scholarly API・web通信は実装しない。

## Checkpoint recovery

`--resume`はfailed checkpoint専用。保存済みTaskSpec・context・final responseを再利用して再検証し、
受信済み内容を再生成しない。前回のattempt数・token usage・code hashes・validation gateを履歴として保持し、
全実行を通じて8 attempts以内。コード修正後はoffline全検証を再実行し、新しいgateが必要。
contextが再計算結果と異なる場合や、受信済みresponseのcheckpointが失われた場合はfail closed。
本文のpaired citationはsupported_pointsと照合し、制約欄などの記述的な実在provenance IDも
取得集合内の存在・Knowledge/paragraph関係を確認する。

全4 caseのresponse checkpointがある場合は`--resume --revalidate-only`で、
APIを呼べないモードでschema/citationの再検証のみ行える。フィールド名そのもの
（knowledge_id / paragraph_id）は引用値と区別する。未知の引用値は引き続きrejectする。

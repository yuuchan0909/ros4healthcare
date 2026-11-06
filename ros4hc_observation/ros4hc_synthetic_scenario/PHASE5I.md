# Phase 5I: Evidence-grounded Japanese interpretation MVP

教授向けに1件の実動研究パイプラインを示す。研究性能評価ではない。

Pipeline: Phase 5B structured Sensor Facts → EvidenceQuery → Phase 5H
ConditionAwareRetrievalBaseline → final Evidence Bundle → OpenAI gpt-6-astra
(reasoning_effort=medium) → strict JSON Schema → local citation validation → 日本語表示。
Phase 5C OpenAIProviderを変更せず利用する。temperature/max_output_tokensは指定せず、
store=falseを維持。LLMは検索しない。tools/web/external retrievalは与えない。
実装時のstrict schema仕様確認: https://developers.openai.com/api/docs/guides/structured-outputs

## Input and evidence boundary

既存clean入力のcondition_c_structured.jsonlをファイル順に読み、
available_history_windows == 6の最初のrecordのtask_inputだけを採用する。
ground truthを開かず、sample_id/condition envelopeも送らない。
Sensor Factsは既存Phase 5B再帰allowlistで検査する。
既存literature/realの3 papers / 6 chunksを変更せずverified-only変換する。
EvidenceはPhase 5Hが選んだfinal recordsだけ。audit/mismatch本文、raw registry、
評価正解、分類回答、hidden activity label、API key、不要manifest情報は送らない。
queryはmodalities/variables/temporal_featuresだけを送る。
EvidenceはID/title/text/DOI/limitationsと測定・population・outcome applicabilityを送る。
coverage gapsはquery modality集合からfinal evidenceのsupported modality集合を引いて
ローカルで決定する。現在は4 chunks / 2 papers、HR coverage gapはhr。

## Interpretation limits

partialは測定モダリティの一部に対応するが、変数・測定手順等の直接対応は不完全/未確認という意味。
個人への直接適用を意味しない。population_status=unknownでは対象者への適用可能性は未確認。
合成活動量は歩行速度等に変換しない。個人の診断、個人の転倒確率、
高/低リスク判定を要求しない。外部知識やEvidenceにない医学的事実で補完しない。
3-paper corpusは小規模であり、網羅的レビューではない。HRを支持する文献は現corpusにない。

citations are locally validated: 応答の全paper_id/chunk_id組をモデルへ実際に渡した
final Evidence集合と照合する。架空/mismatch/audit-only引用が1件でもあればvalid resultとしない。
重複引用は複数の主張として安全に許容する。表示title/DOI/applicabilityはローカルから付加する。
これは引用IDの整合性確認であり、主張の意味的entailmentを保証しない。
回答内容の診断的断定等はpromptで制約するが、意味的安全性の自動検証は未実装。

## Execution

リポジトリrootから（出力先はGit外・空directory、上書きしない）：

```bash
export PYTHONPATH="$PWD/ros4hc_observation/ros4hc_synthetic_scenario${PYTHONPATH:+:$PYTHONPATH}"
python3 -m ros4hc_synthetic_scenario.evidence_grounded_interpretation \
  --dry-run --output /tmp/ros4hc_phase5i_dry_run
source ~/.config/ros4hc/openai.env
python3 -m ros4hc_synthetic_scenario.evidence_grounded_interpretation \
  --output ~/ros4hc_experiments/phase5i/astra_grounded_mvp_20261106/run_unique_name
```

liveは既存providerが環境変数OPENAI_API_KEYを読む。値は表示/保存しない。
dry-runはkey不要・provider callゼロ。sensor/bundle/request/schema/payloadまで構築する。
通常は1-call demo。TransientProviderErrorのみ最大2試行、PermanentProviderErrorは再試行なし。
JSON/schema/citation不正で成績を改善するための再生成はしない。
出力: evidence_bundle.json、model_input.json、interpretation.json、run_manifest.json、report_ja.md。
bundle artifactには監査情報を保存するが、model_inputのrequestには含めない。
manifestはUTC時刻/commit/入力とbundleとpromptのSHA-256/model/設定/質問/件数/gaps/usageを記録する。
sensor SHAは選択されたSensor Factsのcanonical JSON、bundle SHAもcanonical JSON、prompt SHAはUTF-8。
失敗時は有効回答なしと記録し、provider例外のメッセージは保存しない。

1件の配線・MVP確認から「RAGにより転倒予測精度が改善した」とは言えない。
研究性能評価は未実施。将来、Sensor-only vs Sensor+Evidenceを事前定義した評価で比較する。

## 2026-11-06 MVP verification

HEAD bdd1570 / human-state-interpretation。focused pytest 19 passed、package pytest
513 passed、isolated colcon build成功、colcon test 513 tests / 0 errors / 0 failures。
dry-run `/tmp/ros4hc_phase5i_dry_run_20261106` は指定の全送信条件に一致した。
live出力先 `~/ros4hc_experiments/phase5i/astra_grounded_mvp_20261106`。
API keyの存在のみbool確認後に実行したが、既存providerがPermanentProviderErrorを返した。
規定どおり再試行していない。有効な日本語回答・引用・token usageは未取得。
エラー本文・認証情報は保存していない。API拒否の詳細理由は未確定。
live/dry-runのsensor/bundle/prompt SHAとmodel requestは一致し、実corpusもHEADから変更なし。
最小MVPのローカル配線検証は完了したが、今回のlive接続成功は確認できていない。

## Live failure diagnosis (2026-11-06)

API通信なしの再監査でstrict schemaとprovider直前requestの構造に問題は見つからなかった。
全2 objectでadditionalProperties=false、全9 propertiesがrequiredと一致。
schemaは776 bytes、schema node最大深度4、非対応keyword/構造なし。
request keysはprompt/output_schemaのみ、decodingはreasoning_effort=mediumのみ。
保存済みpayloadは現providerの_payloadで再構築した値と一致した。

既存providerを変更せず、/tmpの一時scriptで極小schemaのAstra smokeを1回だけ実行した。
HTTPErrorの許可項目を同じ通信から取得し、追加の診断requestは送っていない。
結果: PermanentProviderError、HTTP 401、error.type=invalid_request_error、error.param=null。
API error message: "Your API key has expired. Create a new API key to continue."
現在の接続失敗はAPI keyの期限切れによる認証問題。最初の失敗時にはHTTP詳細を保存して
いないため、その時点のHTTP理由そのものは遡及確定できないが、現在の再現で認証障害を確認した。
API keyの値、headers、Authorization、response全文は表示/保存していない。

指定された停止条件に従いexact Phase 5I schema smokeおよびlive MVPの再実行はしていない。
schema自体のAPI受付は未検証。Phase 5Iコードの修正は不要で、既存失敗成果物も保持した。
有効なAPI keyを実行環境に設定した後に、残りのschema smokeとliveを再開する必要がある。


## Live recovery and semantic audit (2026-11-06)

以前の401（token_invalidated / expired keyとして報告された失敗）はAPI key認証問題であり、
Phase 5I実装の失敗ではない。新しい有効keyでWSL本体のminimal smokeがHTTP 200 / completedで成功した後、
Phase 5I exact output schema smokeも成功し、live MVPもAPI呼出し1回で成功した。
keyの値は記録しない。以前の失敗runは削除せず保持する。

成功run: `~/ros4hc_experiments/phase5i/astra_grounded_mvp_20261106/run_20261106T042640Z`。
model=gpt-6-astra、reasoning_effort=medium、temperature/max_output_tokensなし、store=false、tools/webなし。
Evidence=4 chunks / 2 papers、all Evidence partial、population applicability unknown、coverage gap=["hr"]。
citation validation成功、invalid citation=0。live usage: input 3394 / output 1593 / total 4987 tokens。

初回成功回答がtrendを「合成データに付与されたラベル」と記述したため、promptに最小の意味制約を追加した。
Sensor Factsは提供された観測情報として扱い、ground-truth labelと呼ばず、明示されない生成方法を推測しない。
trendは提供された時間変化情報であり、activity trendのstableをfall riskのstableと解釈しない。
syntheticと明示されてもtrend自体を「付与されたラベル」と表現しない。
ground truth / hidden activity label / sample_idの非送信、Evidence限定、引用検証、決定的promptは維持する。
前節の「未検証」「再開が必要」は認証失敗時点の記録であり、上記成功によって解消した。


### Prompt修正後の再検証

focused pytest 20 passed、package全pytest 514 passed、git diff --check成功。
変更はPhase 5I prompt制約・そのtest・本書のみ。provider / Phase 5H / real corpusは変更なし。
dry-run `/tmp/ros4hc_phase5i_semantic_dry_20261106` は前回成功runとSensor Facts / Evidence bundle /
schema / API設定が一致し、変更はprompt制約のみ。live送信前にもdry-runとのdata/request完全一致を確認した。

新規run: `~/ros4hc_experiments/phase5i/astra_grounded_mvp_20261106/semantic_run_20261106T043009Z`。
同じ設定でlive API呼出し1回、成功。usage: input 3491 / output 1775 / total 5266 tokens。
Evidence=4 chunks / 2 papers、全件partial、population unknown、coverage gap=["hr"]を維持。
引用4件すべて送信Evidence内、invalid / unsupported citation ID pair=0。
回答を目視でsemantic auditし、誤解を招くlabel表現なし、生成方法の推測なし、
fall riskの高低・増減の断定なし、個人の転倒確率の提示なし、partialの直接適用なし、
population unknownとHR gapの明示を確認した。引用ID検証は主張の意味的entailmentを保証しない。
既存失敗run・初回成功runは保持。commit / pushは実行していない。

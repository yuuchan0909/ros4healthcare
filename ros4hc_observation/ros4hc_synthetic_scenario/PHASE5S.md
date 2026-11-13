# Phase 5S — End-to-end comparison pilot

## Research question and scope

固定corpus・固定requestのpilot条件において、明示的なTask/Connector/Knowledge routingがSimple RAGと比べて、どのケースで関連性・groundedness・不足時の回答制限に差を生むかをケースベースで評価する。統計的有意差、一般的優越性、科学的・臨床的妥当性を証明する実験ではない。Aが勝つことを成功条件にしない。

Phase 5K/Nの配線smoke、5O–5Qのtaxonomy/言い換えstress、5Rの抽出単体監査とは異なり、同じ10要求に対するA/Bの取得根拠と回答の差、失敗の伝播を記録する。意味保存パラフレーズの追加、sensor_history、新しいcorpus取得、production機能追加はしない。dry-run/mockは配線確認であり研究結果ではない。

## Prospective Gold

`evaluation/phase5s_cases_gold.json`にS01–S10の要求文と事前設計Goldを固定する。これは設計段階のhuman + ChatGPT-assisted annotationであり、回答出力の最終human評価ではない。要求文とGoldは分離したpublic projectionから扱い、Router・回答モデルへGold/rubricを送らない。一次監査には全出力freeze後に最小rubricのみを渡し、Gold全体は送らない。protocolはphase5s-pilot-v2。今回の変更はlive出力を見ない事前改訂である。

Gold schema: protocol, annotation_status, notice, cases。各caseはsample_id/request/goldを持つ。goldはallowed_task_types, required_information, optional_information, expected_connectors, optional_connectors, expected_domains, allowed_answer_states, required_outputs, expected_insufficiency_reasons, required_evidence_groups, reference_knowledge_ids, forbidden_claimsに加え、後述のrubric各fieldを持つ。

Goldのバイト列SHA-256を評価コードの`GOLD_SHA256`へ固定し、変更時は起動を拒否する。実行前にコピーとhashをGit外へ保存し、各生成call前・実行後も再確認する。起動時・実行中のhash不一致は専用IntegrityViolationでrun全体を即abortし、通常の行単位exceptionとして継続しない。実行中ならmanifest.status=integrity_failureを保存し、次のAPI callは行わない。出力後にGoldを変更して同一protocolの再評価として実行することを許さない。新protocolには明示的なコード・Goldのレビューが必要。このhashは管理者による改変を防ぐ暗号署名ではない。

根拠groupは正解IDの一意一致ではなく、同内容を支持するparagraph/chunk群を表す。reference-group coverageは補助的な機械照合で、意味的な網羅性・関連性の最終判定ではない。

| ID | 内容 | 期待状態 |
| --- | --- | --- |
| S01 | CPGパラメータ調整手順 | answerable |
| S02 | quadruped/CPGの統計解析 | answerable |
| S03 | 関節力・CoTの測定/推定 | answerable |
| S04 | 不整地の経験的比較結果 | answerable |
| S05 | fall文献検索 | answerable（検索結果の提示） |
| S06 | Parkinson研究根拠 | insufficient |
| S07 | quadruped方法＋人間の転倒への適用根拠 | partial（回答可能部分の提示が必要） |
| S08 | センサー選定根拠・要求性能 | partial（回答可能部分の提示が必要）、capability不足 |
| S09 | 実験自動化の方法・解析・能力 | partial（回答可能部分の提示が必要）、connector不足 |
| S10 | ドル円予測 | reject |

全10要求の正確な文面・TaskSpec許容集合・情報要求・connector・domain・回答制限はJSONを参照。S01のscholarly_evidence追加、S08のscholarly_evidence追加は事前に許容。取得できる部分の放棄は、不正な一般化を避けたこととは別に記録する。

## Conditions and unchanged production boundary

A: `task_router.make_request/parse_task`と同じstrict validatorで自然言語からTaskSpecを生成し、既存`grounded_research_assistant.bounded_context`を呼ぶ。この関数内のdeterministic registry、type filtering、domain guard、重複排除、8-record上限は変更しない。取得structured Knowledgeの全内容フィールド・population・type・provenance・未検証状態を評価contextへ保持する。元paragraph本文をAへ追加しない。

B: requestだけを固定の直接検索器へ渡す。CPGの`role=body`の全source paragraph（表・式・captionを含む）と、Aと同じ既存verified-only fall evidence chunksを検索する。TaskSpec、connector結果、Knowledge record、type filter、domain guard、Goldに依存しない。Aが内部で既定fall corpusを読むため、異なる`--corpus`指定は拒否し、A/Bのsource集合を一致させる。新しい全文や未検証fall chunkを追加しない。

C: requestのみ。retrieval、corpus、TaskSpec、context、Web、toolsなし。CはA/Bと公平なretrieval比較ではなく、補助的なClosed-book参考条件。Cのretrieved-context GroundednessとEvidence relevanceはN/A。

既存productionの最終schemaはmachine Knowledge専用であり、Bのparagraph/fall引用と共通化できない。この評価に限りA/B共通の最終回答renderer・schemaを使う。production Router/connector/extraction/final renderer自体は変更しない。従ってAは「既存routing/retrieval＋共通評価回答境界」であり、Phase 5N CLIをそのまま実行した条件ではない。Aのmachine引用sliceは既存`validate_response`へproduction schemaに投影して補助検証する。この投影は元回答全文の意味的検証ではない。projection失敗はsupplementaryに保持する。

## Common answer boundary and context budget

A/Bは同じ`gpt-6-astra`、reasoning_effort=medium、store=false、tools/Webなし、同じgrounding/不足明示instruction、概ね800字以内の日本語回答を使う。内容改善・schema修正の再生成、通信retryともに行わない。Cは同じ回答モデル・設定・長さの目安だが、根拠がないことを明示する別instructionを使う。

共通schema: answer_ja, citations[{statement_ja, source_id, paragraph_id}], limitations, insufficient_information[{reason, detail_ja}]。評価状態記録のためanswer_state（answerable/partial/insufficient/reject）も持つ。source_idはmachine Knowledge ID、元paragraph ID、または`fall:paper_id:chunk_id`。引用構造の正しさと意味的支持は別である。

A/B contextは最大8件。context JSON全体（Aのrouting metadata、gapを含む）に同じ16,000 UTF-8 bytes上限を適用する。これはtoken上限そのものではなく、実測token数でもない。tokenizerは追加しない。実際のtoken利用量はprovider usageへ別記する。同じbytesでも情報量・token数・回答に使える内容量は等しくならない。

順位順の候補を原子的に追加し、上限を超えるrecordは本文を切らず除外する。除外ID、利用byte上界、pipeline_limitationを記録する。Aのmetadataも予算に含むのでBと等しい内容量になるとは限らない。上限・8件制約による欠落を取得失敗と混同しない。

各A/B行のbudgetへcandidate_evidence_count、selected_evidence_count、selected_context_bytes、dropped_by_top_k（ID一覧）、dropped_by_byte_budget（ID一覧）、candidate_item_bytes/context_item_bytes（各source_idとcanonical JSONのUTF-8 bytes）を保存する。selected_context_bytesにはmetadata、JSON構文も含むためitem bytes合計とは異なる。従来のused/dropped_source_idsも保持する。B候補は正score全件であり、順位・上位8件選択を変更しない。A候補はconnectorが返したrecordの重複除去後、productionのglobal 8件配分前の集合。connector内部の検索・型filter・local top-k以前の全KBを候補件数と呼ばない。candidate_stageにこの境界を記録する。Aのglobal上限で消えたIDと後段byte上限で消えたIDを分離する。

これはsystem-level comparisonでありrouting単独のablationではない。Aだけ追加metadata（TaskSpec・connector status等）を持つことも提案pipelineの一部である。これらは削除せず、予算に含める。

curated fallはA/Bとも検索結果・出典の提示に限定し、このpilotでは科学的内容を要約しない。machine Knowledgeを使う回答には既存の未検証warningを要求する。未取得知識、事前学習知識、別domainでの補完は禁止する。

## Frozen Simple RAG ranking

`SEARCH_PROTOCOL`をmanifestへ保存しhash固定する。NFKC＋casefold後に固定の一般aliasを一度だけ展開する。aliasはCPG/四足/転倒、統計/解析/測定/推定/方法/手順等で、全ケース共通。元の表記も残す。case ID、Gold、TaskSpecによるquery rewritingや手動最適化はない。

ASCII英数字語と日本語連続文字列の文字bigramをtokenとし、公開定数STOPの英語stopwordsを除く。documentはtitle + text、queryの語頻度はbinary。日本語alias展開を含む同じtokenizerをdocument/queryに適用する。

BM25: k1=1.2、b=0.75、N=document数、df=語を含むdocument数、dl=document長、avgdl=平均長。

`score = Σ(q unique term) log(1+(N-df+0.5)/(df+0.5)) * tf*2.2 / (tf+1.2*(0.25+0.75*dl/avgdl))`

score>0だけをscore降順、同点はsource_id昇順で並べ、最大8件を取得する。その後共通context上限を適用する。domain不一致でも語彙が一致すれば返り得る。強いsemantic RAG全般を代表するbaselineではない。検索集合の固定snapshot hashを保持し、Goldを見た調整は行わない。

## Insufficiency reasons and failures

複数保存可: no_relevant_knowledge / connector_unavailable / domain_mismatch / retrieval_failure / pipeline_limitation / out_of_scope。

Aのconnector status・既存reasonから実行上の理由を記録し、モデルの不足理由も別の回答欄から併合する。Bで検索結果があることを「関連根拠あり」とは断定しない。no_relevant_knowledgeは空検索時の候補理由で、意味的な不足は監査対象。domain mismatchとSC未実装、検索例外を区別する。unknown taskはout_of_scope候補だが、それが正当かは別評価。

各case/conditionの行は処理前に作成し、routing/retrieval/answerの例外も30行に残す。エラー本文・credentialを保存せず型のみ記録する。受信済みraw応答、schema不備、引用検証不備、失敗stage、usageとwall timeを記録する。失敗を成功分母から落とさない。source/Goldの変更はintegrity_failureとし研究結果を無効化する。再開・上書きはせず別run directoryを使う。

## Four primary metrics

1. Routing correctness: Aのみ。事前Goldに対するtask_type許容集合、required_information/connectorのmissing/unnecessary、target_conceptsの固定domain alias照合を記録する。自由概念の曖昧なdomain意味はspot checkへ委ねる。B/CのroutingはN/A。
2. Evidence relevance: A/Bの各取得根拠をLLMがrelevant/partly_relevant/irrelevantで仮判定。根拠group coverageは別のdeterministic補助値であり、ID一致を意味的関連性と同一視しない。
3. Groundedness: A/B回答の実質的主張をLLMで分割しSupported/Partial/Unsupportedを仮判定。本文だけでなく制約・不足欄も対象。全拒否でunsupportedがゼロでも成功としない。CはN/A。
4. Insufficiency handling: LLMがappropriate/partly_appropriate/inappropriateを事前rubricに基づき仮判定。無理な断定、domain代用、過剰棄却、不足説明を確認。事前Goldのanswer_state一致は補助であり意味的判定ではない。

provenance ID/hash/schema validationは補助検証。総合スコア、統計的有意差、一般的優越性を算出しない。token/call/timeは実行記録。TaskSpec/IDの機械一致、LLM仮判定、人間の確認を混同しない。

## Blinded AI preliminary audit and human spot-check candidates

生成実行とは別コマンドで行う。Rxxxの匿名packetを固定hash順で並べ、condition/sample ID/Gold全体/他条件の回答/Aのrouting metadataをモデルへ送らない。private_mappingは別ファイルに保存する。request、実際に渡したevidence/gap、回答、事前固定rubricで監査する。Cだけreference_onlyを示し、groundednessはnull、evidenceは空にする。形式・根拠表現から条件が推測できる可能性は残り、完全blindingを保証しない。

claimsにはSupported/Partial/Unsupported（Cはnull）、evidenceにはrelevant/partly_relevant/irrelevant、insufficiencyにはappropriate/partly_appropriate/inappropriateと理由を要求する。partial_answer_handlingにはcomplete_supported_part/partial_supported_part/over_rejection/not_applicableと専用理由を要求する。judge_confidence=high/medium/lowとconfidence理由も保存する。根拠全件の評価、未知ID混入、CでのN/Aを検証する。LLM監査は内容の真偽や独立した人間判断ではない。

候補条件: A/Bの仮評価signature（最悪labelだけでなくclaim/evidenceのlabel件数分布・partial判定・confidenceも含む）が異なる、Partial/Unsupported、partly_relevant/irrelevant、partly_appropriate/inappropriate、over_rejection、expected_answer_state不一致、partial期待時の全面拒否候補、domain mismatch、監査不確実性、表・数式の存在、judge_confidence=low、実行/監査/引用の失敗。

S07〜S09はpartial_answer_expected=true、expected_answer_state=partial、allowed_answer_states=[partial]。全面insufficientをstate一致の成功にしない。機械処理は本文からwarning・空白・引用表記を除いた長さ20文字未満、insufficient/reject、取得evidence/citationがあるのに拒否状態、citationなし、を候補signalとして保存する。部分回答のrequired_outputs充足は文字列一致で正解認定できないため、S07〜S09全件をpartial_required_outputs_semantic_reviewとして必ず候補へ入れる。stateがpartialでも実質拒否、citationがあっても回答可能部分が欠落、という場合を取り逃がさない保守的方針である。機械処理のみで意味的正解・不正解を確定しない。生成終了時にもdeterministic_spot_check_candidates.jsonを保存し、監査失敗でも候補を失わない。候補はケース単位で抽出しhuman_review_status=not_startedのまま保存する。人間のspot checkやGold更新は自動化しない。

AIによる一次監査であり、最終human Goldではない。選択的spot checkは全条件共通の誤判定を見逃し得る。未確認結果をhuman-validatedと呼ばない。

## Execution and artifacts

モジュールはsource checkoutから実行する。setup.pyやproduction install構成は変更しない。出力は`~/ros4hc_experiments/phase5s/`以下かつGit外・空のdirectoryだけを許可する。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -B -m ros4hc_synthetic_scenario.end_to_end_evaluation run --output ~/ros4hc_experiments/phase5s/wiring_run
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -B -m ros4hc_synthetic_scenario.end_to_end_evaluation audit --run-dir ~/ros4hc_experiments/phase5s/wiring_run --output ~/ros4hc_experiments/phase5s/wiring_audit
```

既定はdry-runでAPI=0。liveは別途明示的な実行承認後のみ`--live --validation FILE`を使う。validationはfocused_pytest/package_pytest/diff_check/dry_run/source_auditがすべてtrue、現在のevaluation_hashes/source_hashesと完全一致する記録を要求する。gateは自己申告の再実行防止補助であり署名済み認証ではない。providerは既存OPENAI_API_KEY transportを使い、資格情報を成果物へ保存しない。

生成上限40 calls（A Router 10 + 回答30）、一次監査上限30 calls。retryなし。live監査はlive生成結果だけを許可する。今回はlive/API実験を実行しない。

生成成果物: manifest.json, frozen_gold.json, results.json, deterministic_metrics.json, deterministic_spot_check_candidates.json。
監査成果物: manifest.json, private_mapping.json, blind_packets.json, audits.json, case_comparison.json, spot_check_candidates.json。

## Known limits and success criterion

現在のSensorCapabilityConnector/ActionPolicyConnectorは未実装。sensor_historyはPhase 5N入口のSensor Facts制約により今回除外し、sensor対応そのものの比較にしない。multi-domain guardによる全拒否を、部分回答もできた成功と同一視しない。

このpilotは固定された1つのCPG論文と小規模fall corpus、10件、AI-assisted annotation/auditを含む。source textとstructured extractionの表現差、共通評価renderer、lexical baselineの限界を記録する。routing要素単独の因果効果、一般化、科学的・臨床的妥当性を主張しない。

成功とは、事前Goldと条件が固定され、失敗も含む10ケースで差と原因を追跡でき、疑わしい結果のspot check候補を提示できること。A優位、抽出精度100%、独立human evaluationを成功条件にしない。


## Judge-only rubric and freeze boundary

全30行（失敗行も含む）を保存し、manifestをcompleted/completed_with_failures、results_frozen=true、results_sha256固定としてからのみFrozenRunを構築できる。行の組合せ・terminal status・results hash・Gold全体のhash・snapshotを検証し、packet生成・各judge request時にも再検証する。生成処理はpublic projectionのrequestだけをrouting/retrievalへ渡し、回答contextにrubric/Goldのfieldが混入するとAPI送信前に拒否する。生成promptのschema/instructionはA/B共通で、rubricは追加しない。

rubric schema（全field必須、追加field不可）:

```text
expected_answer_state: answer | partial | insufficient | reject
required_outputs: string[]
forbidden_outputs: string[]
forbidden_domain_substitution: boolean
expected_insufficiency_reasons: REASONS[]
partial_answer_expected: boolean
known_evaluation_constraints: string[]
```

生成回答のanswerableはrubricのanswerへ機械比較時のみ対応付ける。rubricは各caseのGoldからallowlistで投影し、expected connector ID、expected domain field、正解evidence/Knowledge IDを含めない。実際に取得・回答で使った出典IDはcontext/回答に残す。能力制約は「センサー能力照会は未実装・利用不可」のように自然言語で記し、特定条件の正誤や期待connector IDを渡さない。同じcaseのrubricは全条件で同一。

S06は現corpusにParkinson根拠なし、S07はquadruped methodがある一方human fallsへの適用根拠なし、S08/S09は計測・手順・解析の既存部分とセンサー能力照会のunavailableを区別する。これらは実験条件であり一般医学知識から推測させない。rubricはGroundednessの引用根拠ではなく、不足・過剰棄却判定の基準。corpusに存在しても未取得の情報で回答を補完させず、retrieval不足と取得済み根拠の無視を理由で区別する。Cはreference_onlyの参考評価で、Groundedness=null、partial_answer_handling=not_applicable。

## Frozen BM25 aliases

NFKC、casefold、非再帰の一回展開、ASCII語＋日本語文字bigram、STOP、BM25の式・tie breakはv1と同一。case-specific alias、paragraph/Gold evidence ID、expected domainによるquery rewritingを追加しない。固定alias一覧:

| 元表記 | 追加語 |
| --- | --- |
| 四足 | quadruped |
| 四脚 | quadruped |
| 中央パターン生成器 | cpg |
| 転倒 | fall |
| パーキンソン | parkinson |
| 統計 | statistical statistics |
| 解析 | analysis |
| 実験 | experiment experimental |
| 方法 | method |
| 手順 | procedure protocol |
| 調整 | tuning adjustment |
| パラメータ | parameter |
| 関節 | joint |
| 測定 | measurement sensing |
| 推定 | estimate estimated |
| 不整地 | uneven terrain |
| 性能 | performance |
| 比較 | comparison baseline |
| 指標 | metric |
| センサー | sensor |
| 歩行 | gait locomotion |
| 制御 | control controller |
| 再現 | reproduce |
| 自動化 | automation |
| 要求性能 | capability specification |
| 選定 | selection |

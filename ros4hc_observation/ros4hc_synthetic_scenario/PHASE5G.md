# Phase 5G — 条件付き転倒リスク文献知識ベースの手動キュレーション


## 教授方針による監査・改訂

研究の流れは、異種センサ → ROS4HC共通Observation → 時系列変化・履歴処理 → Self-Describe → 構造化文献知識層（RAG）→ 根拠付き転倒リスク評価 → 将来のロボット行動。World Modelは主方向ではない。

既存Phase 5GのPaperRecord + EvidenceChunk、DOI正規化、出典、状態管理、verified-only変換、coverage、重複診断は保持した。監査で不足していたのは「どの条件でその所見／閾値が支持されたか」を機械可読にする契約である。EvidenceChunkへ任意の`fall_risk`を追加する後方互換拡張を採用した。従来のフラットなpopulation等はそのまま利用できる。一般的な論文メタデータや自由summaryだけでは、閾値の適用可能性は判断できない。

### 条件付き証拠のスキーマ

`fall_risk_evidence.py`のFallRiskEvidenceRecord / SCHEMAが契約。paper_id / chunk_idは親EvidenceChunkにあり、以下を1つの所見として保持する。1論文内で集団や測定法、閾値の異なる所見はchunkを分ける。未記載のgroup/fieldはnullへ正規化し、メタデータを補完・推定しない。

| group | 主なfield |
|---|---|
| population | description、sample_size、age、setting、inclusion_criteria、exclusion_criteria |
| measurement | sensor_modalities、devices、protocol、measured_variables、test_name、measurement_distance_or_duration、body_location |
| risk_indicator | indicator_name、threshold_value、threshold_operator、unit、unit_required、reference_criterion |
| fall_outcome | fall_definition、ascertainment_method、prospective_or_retrospective、followup_duration |
| results | result_summary、auc、sensitivity、specificity、effect_size、confidence_interval、p_value、p_value_operator、reporting_notes |
| limitations | paper_reported_limitations |
| knowledge_base | risk_factor_category、evidence_strength_note |
| provenance | section、page、table、figure、doi、url |

value_originはpaper_reported / not_reported / unknown / null。inferred、llm_generated、sensor_derived等は認めない。これはキュレーターの申告であり、自由本文の虚偽や未申告の推測を自動検知するものではない。

risk_factor_categoryはmotor_function / fall_history / medication / environment / psychological / physiological / other / unknown / null。判然としない場合はunknownまたはnullとし、自動分類しない。evidence_strength_noteは前向き／後ろ向き研究、外部検証、限られた集団等を人が記述する文字列。数値の品質スコアは導入していない。

age、測定距離・時間、followup_durationは原資料の条件を損なわない文字列。sample_sizeは正整数。criteria / devices / body_location / paper_reported_limitationsは文字列配列。空欄はnull、文字列欄はunknown / not reported / 未報告等も保持し、coverage上は未確認として数える。数値欄の未報告はnullとする。

threshold_valueは有限数値、operatorは < / <= / = / >= / >。範囲や複合基準はreference_criterionに原資料の記載を人がparaphraseして保持し、無理に単一閾値へ変換しない。unit_requiredは人が明示するboolean/nullで、指標名から推測しない。次元のない値はunit="dimensionless"等を人が明示できる。

AUC / sensitivity / specificity / p_valueは有限数値で0〜1。原資料が%表記や別の形式なら、その記載をreporting_notesに残し、数値欄の扱いを人が確認する。自動的な%変換や文字列からの数値抽出はしない。effect_sizeはvalue / measure / unit、confidence_intervalはlower / upper / confidence_level（0〜1）/ measure / unitを持つ構造で、どの尺度・結果の区間かを人が明示する。結果が複数ならchunkを分ける。p_value_operatorで「<」なども保持する。

### 閾値単独では不十分

「ある歩行速度以下なら高リスク」のような無条件の規則は作らない。閾値＋対象集団＋測定protocol＋転倒定義＋追跡期間＋把握方法＋報告性能を一体で保持する。論文にない条件を埋めて完全な規則に見せない。条件欠損は文献の欠点を断定せず、適用可能性が未確認であることを示す。

Phase 5Fへの変換でもfall_risk全体をcuration_provenance.fall_riskに保持し、検索結果ではprovenance.curation.fall_riskから読める。出典位置・DOI・URLも保持する。明示された構造化measurementのmodality/variableとpopulation.descriptionは検索用のフラット項目へ投影するが、構造化条件のnullを論文メタデータで補完しない。旧フィールドだけのレコードは引き続き受理する。旧Phase 5Fの正規化・スコア・順位・出力は維持する。

将来のretrievalは①lexical/semantic候補取得、②既知の対象者・測定contextと集団／測定／outcome条件の適合確認、の2段階を想定する。今回は構造を準備するだけで、条件一致、適用可否、臨床判定を自動計算しない。verifiedは人の出典確認状態であり、個人への適用可能性が確認された意味ではない。

### 警告とエラー

警告: fall_risk未登録、集団description不足、protocol不足、転倒定義不足、追跡期間不足、条件なしの閾値、報告性能のない閾値、未記録の把握時制、unit/operator未記載、結果の出典位置欠落、値の由来未記録。原論文が報告していない項目があっても保存できる。位置不足・summary重複等の既存警告も維持する。警告だけでverifiedへ昇格させることはない。

エラー: 不正型、NaN/Infinity、範囲外の性能値、逆転した信頼区間、不正enum、unit_required=trueなのに閾値のunitが不明、未対応のinferred由来、親論文と異なるDOI、重複ID、孤立chunk、未知のネストfield。未確認・未記載はnullで保存する。単位不明の論文を保存するために単位を創作せず、unit_required=null、unit=nullとして警告を残せる。

coverageには登録全体とverified検索対象の両方で、集団、センサ、変数、protocol、閾値、転倒定義、追跡期間、category、各定量指標、制約、証拠強度記述、出典位置／確認状態の件数を追加した。unknown/not reportedは情報充足と数えない。manifestにもfall_risk_schema_version=phase5g-conditions-v1と条件coverageを追加する。

### Appendix A互換テンプレートと最初の3本

`templates/fall_risk_paper_template.jsonl`はpaperとevidence_chunksをまとめた記入用シートで、題名・著者・掲載誌・年と上記の条件項目を持つ。nullは空欄であり、架空の実論文は入っていない。`templates/fall_risk_paper_template_ja.csv`は日本語見出し、対応JSON path、空の記入行を持つ人手作業用シート。CSVの自動importは行わない。

1. 最初の実論文3本を研究者が読み、1本ずつシートを複製する。
2. 論文メタデータ、対象集団、測定法・適用条件を記入する。
3. 閾値・結果を報告どおりに記録し、測定単位や尺度を明記する。
4. 転倒定義、前向き／後ろ向き把握、追跡期間を記入する。
5. 論文が述べた制約と証拠強度の記述を記録する。
6. ページ・節・表・図とDOI/URLを記録する。
7. 不明はnull / not reportedのまま残す。本文を読まずに穴埋めしない。
8. シートのpaperをpapers.jsonl、evidence_chunksをevidence_chunks.jsonlへ分離し、validatorを実行する。
9. 警告の理由を原資料と照合する。
10. 人が確認してから論文・chunkをverifiedにする。最初はcandidateのまま保存する。

JSONシートを分離するローカル例（sheetは人が記入済みの1論文分、出力先はGit外）:

```python
import json
from pathlib import Path
sheet = json.loads(Path('/tmp/curated_paper_sheet.jsonl').read_text())
Path('/tmp/papers.jsonl').write_text(json.dumps(sheet['paper'], ensure_ascii=False) + '\n')
Path('/tmp/evidence_chunks.jsonl').write_text(''.join(
    json.dumps(chunk, ensure_ascii=False) + '\n' for chunk in sheet['evidence_chunks']))
```

3本分を結合する場合もpaper_idは重複させず、各chunkにその親IDを記入する。既存validator CLIをrealモード（既定）で使う。自動検索・PDF取得・実論文内容の生成はしない。

### Self-Describe・今後のRAG・ロボット

Phase 5B = Step 3aの構造化／テンプレート記述。Phase 5Cの将来のlive LLM = Step 3bの自然言語Self-Describe。Phase 5Bを改名・変更しない。最初のMVPはSelf-Describe → 1回のretrieval → 根拠付き判断 → citation。今回はその前段の文献キュレーションまでで、LLM判断やAgentic RAGは実装しない。将来、欠測や不十分な記述を確認し、別の記述・測定を求めて2〜3回検索する拡張を検討する。

**文献は集団レベルの知見である。「明日の転倒確率=XX%」のような個人の将来確率は、科学的・方法論的に正当化されない限り出力しない。** 将来の出力は関連risk factor、その方向、適用可能な証拠、不確実性・証拠不足を中心に設計する。AUCや感度を個人の転倒確率へ読み替えない。診断は行わない。

将来のロボットinterfaceは、この証拠・適用条件・不確実性を明示した評価を受け取る別層とする。監視・通知・速度低減等の可逆的／低リスク対応から検討するが、制御はPhase 5Gの範囲外。

以下は引き続き利用する一般レジストリ基盤の説明。

## 目的と範囲

Phase 5Fは合成文献でEvidence Query → lexical検索 → EvidenceBundleの契約を検証した。Phase 5Gは実文献を人が確認して登録するためのPaperRecord（論文）とEvidenceChunk（論文内の証拠）の1:Nレジストリを用意する。実文献の検索・PDF取得・自動要約・API・埋め込み・vector DBは実装しない。医学的妥当性、診断、研究品質の優劣を主張しない。World Modelの方向は拡張しない。

実文献は今回0件。デモは3本の架空のテスト論文・5つの架空のテストchunkだけで、すべてsynthetic_fixture=true。**合成テスト資料・研究上の実証的根拠ではない。**

## ファイルとスキーマ

`literature_registry.py`のTypedDictが正規化後の契約。UTF-8のpapers.jsonlとevidence_chunks.jsonlを別々に管理する。空ファイルも有効なレジストリ。templates/papers_template.jsonlとtemplates/evidence_chunks_template.jsonlは各フィールドをnullで空欄にした編集開始用レコードで、架空の学術的記述や実論文は含まない。必須のID・題名・summary等を人が埋めるまでは検証に通らない。初期状態はcandidate、synthetic_fixture=false（実文献登録用）で、chunkの位置確認はfalse。空のregistryから始める場合は空ファイルも利用できる。

PaperRecordの必須情報はpaper_id、title、明示的なsynthetic_fixture boolean。curation_status未記入はcandidate。任意項目はauthors、year、venue、doi、url、publication_type、publication_status、peer_reviewed、study_design、population、sample_size、sensor_modalities、measured_variables、tasks_or_states、topics、external_validation、ground_truth_method、notes、exclusion_reason。

- authors / sensor_modalities / measured_variables / tasks_or_states / topicsは空でない文字列の配列またはnull。
- yearは1〜9999、sample_sizeは1以上の整数またはnull。booleanを整数扱いしない。
- peer_reviewedはbooleanまたはnull。external_validationとground_truth_methodは記述的文字列またはnull。数値の総合品質スコアは作らない。
- publication_type: journal_article / conference_paper / review / preprint / thesis / book_chapter / report / other（またはnull）。
- publication_status: published / preprint / accepted / in_press / retracted / unknown（またはnull）。撤回等の扱いはキュレーターが確認し、必要に応じexcludedにする。自動的な学術品質判定はしない。
- その他の任意項目は文字列またはnull。不明情報はnull/空配列とし、推測で補完しない。
- 実文献IDはpaper_0001など`paper_`＋4桁以上の数字。題名、結論、センサ、状態、順位を符号化しない。fixtureにはsynthetic_paper_0001形式も使える。論文IDは登録後に変更しない。

EvidenceChunkの必須情報はpaper_id、chunk_id、空白でないevidence_summary、明示的synthetic_fixture boolean。curation_status未記入はcandidate、source_location_verified未記入はfalse。

- 任意文字列: section、page（例: "12–13"）、figure_or_table、short_quote、population、task_or_state、curator_notes、exclusion_reason。
- 任意文字列配列: sensor_modalities、measured_variables、topics、limitations。
- finding_type: association / prediction / measurement / validation / comparison / intervention / temporal_pattern / method / other（またはnull）。
- relationship: associated_with / predicts / measured_by / differs_between / changes_with / none（またはnull）。
- direction: increase / decrease / positive / negative / mixed / not_applicable / unknown（またはnull）。
- source_location_verifiedはboolean。section/page/figure_or_tableのどれもないと警告。確認フラグがtrueなのに位置がない場合は矛盾としてエラー。
- chunk_idはpaper_idとの組で一意。別論文の同じchunk_idは許可する。

未知フィールド・不正型・重複JSONキー・必須欠損・不正enum・孤立chunk・ID重複は拒否する。スキーマが認めないevaluator情報をネストした辞書として渡すことも拒否する。検証は最初のエラーで停止し、CLIはvalid=falseのレポートと非0終了を返す。

## 手動キュレーション

1. 研究者が論文を特定する。本ソフトは検索・ダウンロードしない。
2. papers.jsonlへ安定した内部ID、題名、確認できたメタデータ、synthetic_fixture=falseを記入する。
3. 関連箇所を原資料で読む。
4. 何を支持するかを短いevidence_summaryとして自分の言葉で記入する。
5. section / page / figure_or_tableとsource_location_verifiedを記録する。
6. そのchunkに実際に関係するmodality、変数、population、task/topicを記録する。
7. finding_type、relationship、direction、制約を人が判断して記入する。不明ならnull。題名やセンサ値から自動推定しない。
8. 状態を更新し、validatorを実行する。
9. 人が内容・出典を確認した論文とchunkをverifiedにする。
10. verifiedのみ変換し、coverage・検索結果を点検する。

直近の作業は最初の実文献3本の手動キュレーションであり、その後10〜30本へ拡張する。対象領域の例はheart_rate、heart_rate_variability、accelerometry、imu、physical_activity、gait、balance、posture、postural_transition、fall_related_movement、recovery_physiological_response、multimodal_sensing、wearable_sensing、human_activity_recognition、sensor_fusion。topicsはこれらを推奨し、他の明示的な語も許可する。sensor_modalitiesはPhase 5F互換のhr / activityのほかimu / pressure / camera等を人が明示する。accをactivityに自動変換する等の同義語正規化はこの段階では行わない。

populationは原資料で確認できた範囲だけを記録する。センサ種類や題名から年齢・疾患集団を推定しない。論文全体のpopulationやセンサは個別のfindingにも当てはまるとは限らないため、変換時に論文メタデータをchunkの欠損値へ自動継承しない。

## evidence_summary・引用・著作権

evidence_summaryは原資料の支持範囲に限定した**人手のparaphrase**。関連・予測・測定法・検証・介入結果などを、研究デザインが支持する範囲で記述する。関連を因果関係に強めない。short_quoteは任意であり、変換先にはコピーしない。長い段落を不必要にコピーせず、全文論文はこのコーパスへ保存しない。引用は必要最小限にして出典位置を保持し、著作権・ライセンス・アクセス条件を守る。プログラムは著作権適合性や手動paraphraseの正確性を判定できない。

## ワークフロー状態と除外

candidate → metadata_checked → evidence_curated → verifiedを想定するが、ソフトは遷移履歴を管理・強制しない。論文・chunkそれぞれに状態を持つ。excludedは削除せずexclusion_reasonを任意記録し、未記入なら警告する。対象外集団、関連性不足、重複研究、本文アクセス不能等の理由は人が記述する。理由の固定enumや自動判定はない。

通常変換は**論文とchunkの両方がverified**の場合だけ。excludedは常に変換しない。--include-unverifiedは診断・開発用で、diagnostic_literature_corpus.jsonlに出力する。各レコードの出典に実際の状態とdiagnostic_unverifiedを保持する。通常のverified_literature_corpus.jsonlとは区別する。既存ディレクトリ内の過去の別モードのファイルは削除しないため、研究実行ごとに新しい出力先を使う。

## DOI・provenanceとPhase 5F互換性

DOIのhttps://doi.org/...、http(s)://dx.doi.org/...、doi:...、bare DOIを空白除去・小文字化してbare形式へ正規化する。DOIは任意。ネットワークで実在性を検証せず、基本構文だけを検査する。同じ正規化DOIを異なる内部paper_idに登録するとエラー。

変換はEvidenceChunk.evidence_summaryをPhase 5Fのevidence_textへ写し、PaperRecordのID・題名・著者・年・DOI・URLとchunkのpopulation・センサ・変数・task・findingを保持する。curation_provenanceという任意の構造化拡張にsection / page / figure_or_table / source_location_verified / relationship / direction / limitations / curation_status / paper_curation_status / diagnostic_unverifiedを保持する。

Phase 5F EvidenceRecord.provenance.curationにもこの情報を渡すため、EvidenceRecord → ID対 → EvidenceChunk → paper_id → PaperRecord → DOI/URL/位置と追跡できる。元レコードのSHA-256にも拡張フィールドを含む。curator_notes、paper notes、exclusion_reason、short_quoteなどは検索本文やBundleへコピーしない。finding_typeは既存evidence_typeに保持する。既存のPhase 5Fレコードは拡張がなければnullフィールドも追加せず、正規化・スコア・順位・ハッシュを維持する。

realモードはsynthetic_fixture=trueを拒否し、syntheticモードはfalseを拒否する。混在コーパスを許可するオプションはない。明白なsynthetic/fake test表記をfalseに変えた場合も拒否するが、任意の捏造文献を自動検出できるわけではない。実在性・出典の確認は人の責任となる。

## Coverageとmanifest

coverage_report.json / coverage_report_ja.mdは評価側の診断であり、Bundleに入れない。

- 登録全体と、verified-only検索対象を分けて集計する。
- 論文・chunk別にmodality / 変数 / population / task / topic / status、論文の出版年、chunkのfinding_typeを数える。不明は<unknown>。
- paperごとのmodality/topicの行列を作る。複数chunkのmodality集合は同一findingでのクロスモーダル関係を意味しない。
- 各論文／chunkが明示した複数modalityを別々に列挙する。確認済みchunkの組合せとHR＋activity同時記載の有無も報告する。因果的な関係やsensor fusionの有効性は推論しない。
- 推奨topicごとにverified証拠を持つ論文数が0〜1なら「現在のコーパスではcoverageが限定的」と報告する。仮説を証明する文献の収集を要求しない。
- 同一paper/page/完全一致summaryと、casefold・空白正規化後の同一summaryを警告する。語義ベースのAI重複判定はしない。重複が自動削除されることはない。

literature_manifest.jsonはcorpus_version/schema_version=phase5g-v1、mode、正規化レジストリのSHA-256、変換設定、論文/chunk件数、それぞれのverified件数、検索対象件数、synthetic_fixture_count / real_record_count、coverage要約を持つ。synthetic/realのrecord件数は論文＋chunkの合計。verified_chunk_countはchunk自身の状態を数え、実際の検索対象件数はretrieval_chunk_countで区別する。時刻を埋め込まず、順序を固定し、同じ内容なら入力行の並びが変わってもバイト同一にする。Git hashだけに依存しない。

## CLI

パッケージをビルド・source後、リポジトリルートで:

```bash
ros2 run ros4hc_synthetic_scenario demo_curated_literature --output /tmp/ros4hc_phase5g
ros2 run ros4hc_synthetic_scenario validate_real_literature_corpus --papers /tmp/ros4hc_phase5g/papers.jsonl --evidence /tmp/ros4hc_phase5g/evidence_chunks.jsonl --output-report /tmp/ros4hc_phase5g/validation_report.json --mode synthetic
ros2 run ros4hc_synthetic_scenario convert_curated_literature --papers /tmp/ros4hc_phase5g/papers.jsonl --evidence /tmp/ros4hc_phase5g/evidence_chunks.jsonl --output /tmp/ros4hc_phase5g --mode synthetic
```

実文献では--modeを省略する（既定real）。通常コーパスはverified_literature_corpus.jsonl。Phase 5Fのretrieve_local_evidenceの--corpusへそのまま渡せる。デモは3論文（verified / candidate / excluded）、5chunkから2件だけ変換し、Phase 5FのLexicalRetrievalBaselineとbuild_bundleでdemo_retrieval.jsonlを生成する。全レコードは合成であり、取得件数や順位を科学的検索性能と解釈しない。

出力はpapers.jsonl、evidence_chunks.jsonl、validation_report.json、literature_manifest.json、verified_literature_corpus.jsonl、coverage_report.json、coverage_report_ja.md、demo_retrieval.jsonl。Git外の/tmpへ置く。

## 将来のLLM接続と制約

接続点はPhase 5F EvidenceBundleの後段。API取得後にSensor Facts onlyとSensor Facts＋Evidenceの比較を行い、hallucination、unsupported inference、引用と主張の整合性、情報不足時の応答を別途評価する。本実装はAPIもLLM実験も含まない。

センサ入力はPhase 5Fの再帰的allowlist・履歴検証を再利用する。正解・隠しscenario・expected_state・split・future targetの未知フィールドをBundleに通さない。自由な学術本文の中に紛れた命令、誤記、過大解釈、評価情報の文章表現を完全検知する機能ではない。人手の確認が必要。lexical baselineは英字tokenによる工学的基準であり、semantic retrieval、研究品質評価、医学的妥当性評価ではない。

## 分離build/test

```bash
source /opt/ros/humble/setup.bash
colcon --log-base /tmp/ros4hc_phase5g_colcon/log build --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/ros4hc_phase5g_colcon/build --install-base /tmp/ros4hc_phase5g_colcon/install --packages-select ros4hc_synthetic_scenario
source /tmp/ros4hc_phase5g_colcon/install/setup.bash
colcon --log-base /tmp/ros4hc_phase5g_colcon/test_log test --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/ros4hc_phase5g_colcon/build --install-base /tmp/ros4hc_phase5g_colcon/install --packages-select ros4hc_synthetic_scenario
colcon test-result --test-result-base /tmp/ros4hc_phase5g_colcon/build --verbose
```

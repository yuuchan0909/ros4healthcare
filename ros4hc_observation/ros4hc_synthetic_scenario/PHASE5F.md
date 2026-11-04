# Phase 5F — Literature / Evidence Interface

## 目的と研究方向

異種センサ → 共通Observation → Sensor Facts → Evidence Query → ローカル文献chunk → EvidenceRecord → EvidenceBundleという、モデルに依存しない接続契約を用意する。Phase 5Fの範囲は事実から検索・出典付き候補の取得まで。人の状態の結論、診断、医療推奨は生成しない。Phase 5D/Eの時間・複数日データ基盤は残すが、World Model方向は拡張しない。

OpenAI APIアクセスがなくても契約、漏洩防止、再現性を確認できるよう、Python標準ライブラリだけで完全オフライン実行する。ネットワーク、APIキー、GPU、埋め込み、vector DB、外部検索は不要。

## Sensor Factと解釈

Phase 5Bの構造化task_input（hr/acc、条件C/Dの過去から現在のwindows）をそのまま受け取る。Phase 5BのHR_FIELDS/ACC_FIELDSと再帰的audit_inputを再利用する。日本語の自由文入力は受け付けない。Phase 5Bエクスポートのsample_id/conditionは評価側envelopeとして除去し、task_inputだけを利用する。その他の未知キーは黙って除去せず拒否する。

82 bpm、前窓との差+8 bpmは測定事実であり、頻脈、運動、疲労、病気などの結論ではない。値が大きくても検索語を診断語に変換しない。人口集団、疾患、年齢、服薬、転倒リスク、フレイルは推測しない。入力に正解・シナリオ・摂動・将来target・split等の未知キーがあれば、ネスト内も拒否する。履歴は相対時刻が過去から現在まで単調増加し、最後が0であることを検証する。絶対時刻付き履歴も順序を検証する。タイムスタンプのない現在窓の値が本当に取得時点の値かは呼び出し側が保証する。

## データ契約

`evidence.py`のTypedDictにEvidenceQuery / EvidenceRecord / EvidenceBundleを明示する。

Evidence Query:

- modalities: hr / activity（Phase 5Bのaccから正規化）。実際の測定フィールドがある場合のみ追加。
- variables: 存在する測定変数名。null数値とunavailable傾向は検索に利用しない。
- temporal_features: increasing / decreasing / stable。明示された活動傾向、またはhr_change_bpmの符号による方向のみ。
- keywords: heart rate / heart rate change / physical activity / movement intensity / temporal change / multimodal sensingの決定的な部分集合。
- population / clinical_condition: null。
- observed_values: 数値はここに分離。履歴の場合は窓の0始まり位置をキーにする。元の相対時刻・メタデータはsensor_factsに保持。

Literature CorpusはUTF-8 JSONL、1行1短い証拠chunk。必須はpaper_id / chunk_id / title / evidence_text（空白だけの文字列不可）。任意項目はauthors（文字列配列）、year（1〜9999の整数）、doi（bare DOI、10.数字/接尾辞）、url、source_type、population、sensor_modalities（文字列配列）、measured_variables（文字列配列）、task_or_state、evidence_type、notes、synthetic_fixture（boolean）。任意項目はnullまたは空値で欠損を表せる。未記入項目は正規化時にnullとし、メタデータを創作しない。未知フィールド・型不正・必須欠損・空本文・不正year/DOI・(paper_id, chunk_id)重複を拒否する。chunk_idは論文内で一意で、別論文との同名chunkは許容する。DOIは必須ではなく、構文検証は出典の実在確認ではない。

EvidenceRecord:

- paper_id / chunk_id / title / evidence_text
- provenance: paper_id / chunk_id / title / authors / year / doi / url
- corpus_record_sha256: null補完後のコーパスレコードのハッシュ（JSON sort_keys=True, ensure_ascii=False、標準区切り）
- synthetic_fixture: 元レコードの明示フラグまたはnull。nullは実在文献を確認した意味ではない。
- retrieval_score / matched_terms
- sensor_relevance: matched_modalities / matched_variables
- limitations: lexical手法の制約と、fixtureなら合成表示

匿名の本文は返さず、元コーパスのID対とハッシュで追跡可能にする。返却レコードに人の状態の結論を加えない。

EvidenceBundle:

- query: 上記EvidenceQuery
- sensor_facts: 入力事実をdeep copyで保持
- evidence: EvidenceRecord配列
- retrieval_metadata: method=lexical_baseline / top_k / corpus_record_count

ground truth、evaluator target、scenario、perturbation、future target、splitは含めない。利用するのはこのBundle自体であり、評価側envelopeは将来LLMに渡さない。実文献の自由本文に隠れた指示や誤情報がないことまでは保証しないため、将来の実文献導入時には手動キュレーションが必要。

## LexicalRetrievalBaseline

英小文字の英字token集合を作り、query.keywordsとtitle/evidence_textの共通token数に、matched modalities数×4、matched variables数×2を加算する。数値・人口集団・疾患はスコアに使わない。同点はpaper_id、chunk_idの辞書順。正スコアのみ最大top_k件を返す（top_kは0以上の整数）。matched_termsは実際に本文・題名と一致したtoken、メタデータ一致はsensor_relevanceに分ける。未知・metadataのみの入力はキーワードも結果も空になる。

これは契約検証用の単純なlexical baselineであり、semantic retrieval、最終RAG、科学的関連性の保証、最先端手法ではない。英字token方式は日本語検索に適さず、同義語や否定、因果性、研究品質、集団適合性を評価しない。

## 合成fixture

`ros4hc_synthetic_scenario/fixtures/synthetic_literature_corpus.jsonl`は10件。すべてsynthetic_paper_*、Synthetic test document、synthetic_fixture=trueとして明示する。

**合成テスト文献。研究上の実証的根拠ではない。** 人工的なテスト条件Aとの関連という架空記述であり、医学的主張や実論文の代用ではない。合成であることが分かるID/本文にフラグがない場合は拒否する。任意の未申告の架空文献を自動判定するものではない。

## CLIとオフラインデモ

パッケージをビルド・sourceした環境で実行する。出力はGit外の/tmpに置く。

```bash
ros2 run ros4hc_synthetic_scenario validate_literature_corpus --corpus /tmp/ros4hc_phase5f/synthetic_literature_corpus.jsonl
ros2 run ros4hc_synthetic_scenario generate_evidence_queries --sensor-input /tmp/ros4hc_phase5f/sensor_inputs.jsonl --output /tmp/ros4hc_phase5f/evidence_queries.jsonl
ros2 run ros4hc_synthetic_scenario retrieve_local_evidence --sensor-input /tmp/ros4hc_phase5f/sensor_inputs.jsonl --corpus /tmp/ros4hc_phase5f/synthetic_literature_corpus.jsonl --output /tmp/ros4hc_phase5f/evidence_bundles.jsonl --retrieval-output /tmp/ros4hc_phase5f/retrieval_results.jsonl --top-k 5
```

診断はquery数、コーパス数、取得件数、0件query数、一致語数、出典完全性、順序の再現性を数える。合成fixtureの順位テストは工学的テストであり、科学的検索性能ではない。

## 将来の実文献とLLM接続

実文献は人が短い証拠chunkまたは研究ノートとしてキュレーションし、上記JSONLに変換してvalidatorに渡す。synthetic_fixture=falseは人が実在出典を確認して設定する。集団、タスク、制約、証拠種別は元資料にある範囲だけ記録する。DOIがない有効な資料も取り込める。コーパスは全文論文の保存基盤ではなく、著作権のある全文を不必要に複製しない。引用・出典を保持し、原資料のライセンス・アクセス条件を守る。

将来のOpenAI API接続点はEvidenceBundleの後段。現段階ではAPI呼び出しも新規LLM実験も実装しない。Phase 5CのSensor Facts only条件は変更せず、将来Sensor Facts + Evidence条件と比較できる。評価では解釈品質、hallucination、根拠のない推論、引用と主張の整合性、情報不足時の応答を別途測る。検索候補の存在を人の状態や医療的結論の支持と同一視しない。

再現可能な4入力のデモ（Phase 5A生成 → Phase 5B make_inputで事実化。HRのみ・活動量のみ・両方・低情報）:

```bash
ros2 run ros4hc_synthetic_scenario demo_local_evidence --config ros4hc_observation/ros4hc_synthetic_scenario/configs/daily_subject_001.json --output /tmp/ros4hc_phase5f
```

このコマンドはコーパスのコピー、sensor_inputs.jsonl、evidence_queries.jsonl、retrieval_results.jsonl、evidence_bundles.jsonl、retrieval_summary_ja.mdを生成する。評価正解を出力しない。診断にはコーパス逆順時の同一結果確認も含む。

分離build/test例（リポジトリルート、ROS Humble環境）:

```bash
source /opt/ros/humble/setup.bash
colcon --log-base /tmp/ros4hc_phase5f_colcon/log build --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/ros4hc_phase5f_colcon/build --install-base /tmp/ros4hc_phase5f_colcon/install --packages-select ros4hc_synthetic_scenario
source /tmp/ros4hc_phase5f_colcon/install/setup.bash
colcon --log-base /tmp/ros4hc_phase5f_colcon/test_log test --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/ros4hc_phase5f_colcon/build --install-base /tmp/ros4hc_phase5f_colcon/install --packages-select ros4hc_synthetic_scenario
colcon test-result --test-result-base /tmp/ros4hc_phase5f_colcon/build --verbose
```

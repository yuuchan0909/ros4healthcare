# Phase 5C — LLM状態解釈の実験runner

## 研究目的と範囲

Phase 5B入力→LLM応答→厳密な分類検査→決定的採点の配線を準備します。今回の環境調査ではAPIキーとモデル設定は未構成、127.0.0.1:11434のOllamaも利用不可でした。**実LLM接続・推論は行っていません。** mockは固定で判断不能を返す配線確認専用で、研究上の分類性能を表しません。

RAG、文献検索、GraphRAG、fine-tuning、画像入力、実Polar評価、診断、臨床判断、production HumanStateHypothesisは実装しません。production ObservationとPhase 5A正規生成・正解を変更しません。将来は実モデルでの比較の後に文献grounding比較へ進む予定です。

## 比較の意味

- A: 前窓差・trendを含む現在窓要約。メタデータ・明示的履歴列なし。
- B: Aと同じ要約＋自己記述メタデータ。A対Bはメタデータ追加の比較。
- C: B＋過去最大30分の明示的相対履歴。B対Cは複数窓文脈追加の比較。
- D: C＋絶対時刻。C対Dは時刻追加の比較。
- structured対日本語: 同じ事実の表現形式の比較。追加センサ情報の比較ではない。

A/Bにも局所変化という時間情報があります。A対Cを「時間情報なし対あり」と呼びません。Dは固定日程の時刻パターンを利用でき、センサのみの推論とは解釈できません。

## pilotとfull

標準は `--mode pilot --dataset clean` です。seed=42でIDのハッシュ順から低活動16、軽活動16、歩行様8を選び、高活動6・回復6をすべて含めます。**52窓×8設定=416リクエスト**。全条件・表現で同じ窓を使用し、選択IDを評価者専用manifestに保存します。先頭窓からの単純切出しではありません。

`--mode full` を明示すると清浄288窓×8設定=2304件を構築できます。pilotからfullへ自動遷移しません。将来live adapterを追加した場合も、まずdry-run→1〜2件の接続確認→pilotを検証し、fullは明示指定する運用にしてください。今回はfullの実推論も行いません。

清浄日の分布は198/66/12/6/6/0窓で、強い不均衡と小さい稀少クラスsupportがあります。pilotは層化され、日全体とは異なる分布なので両者のaccuracyを同じ母集団の値と扱えません。連続窓は独立ではなく、値範囲も分離した固定日程fixtureです。パイプライン検証であり一般化の証拠ではありません。

`--dataset perturbations` は別出力先で指定します。清浄データと摂動データを混ぜて採点しません。摂動版Phase 5Bディレクトリからcleanを指定した場合はclean行だけを抽出します。今回の初回dry-runは清浄データだけです。摂動pilotの高活動・回復は利用可能な全件を選ぶため、52窓という上限は清浄版に限ります。

## provider境界と秘密情報

`llm_providers.py` の `LLMProvider.generate(request, decoding)` / `model_identifier()` / `provider_identifier()` が小さな境界です。generateが受け取るrequestはpromptとoutput_schemaだけで、評価情報は渡しません。`LLMResponse` はraw_responseと任意のusageを持ちます。runnerと採点は特定SDKに依存せず、Python標準ライブラリのみを使います。

- `MockProvider`: 固定応答。外部通信なし。
- `ExternalProvider`: 他環境で得た応答の取り込み用識別子。通信しない。
- live adapter: 今回は安全に利用可能な接続設定がないため未実装。APIキーを設定するだけで現CLIからlive呼出しできる構成ではありません。

環境変数の値や資格情報を保存しません。provider例外の本文・tracebackは保存せず、エラーカテゴリだけを記録します。任意のusage属性をそのまま転記せず、非負整数のinput_tokens/output_tokens/total_tokensだけを保持します。モデル応答は修復せずrawのまま保存するため、外部応答の取り込み時は資格情報を含まない実際のモデル本文だけを渡してください。

## canonical requestと漏洩防止

`build_request(task_input)` が唯一のrequest builderです。全条件・表現でPhase 5Bと同じ分類指示と全6候補の共通出力契約を使います。違いは観測本文のみです。説明契約はPhase 5Bに残りますが、runnerのtaskはclassificationに固定し、説明の大量実験を実行しません。

観測本文には既存のキー許可リスト・英日意味的ラベル監査を適用し、さらに評価ID・窓ID・対象者ID・摂動名を拒否します。初期構築時とprovider呼出し直前に検査し、不正なら送信しません。候補一覧はタスク定義なので許可し、監査対象の観測本文から分離します。正解・元の正解・選択根拠はmanifestにだけ保持します。

## 記録と再現性

manifestにはexperiment_id、UTC timestamp、git commit、未コミット実装も識別するモジュールSHA-256、provider、正確なmodel ID、条件、表現、classification、decoding、窓数、request数、prompt version/hash、入力ファイルSHA-256、選択seed/ID、pilot/full、clean/perturbationsを保存します。既存manifestと条件・入力が異なる場合は同じ出力先で再開できません。

標準temperature=0。`--provider-seed` を指定するとdecodingに含めますが、adapterが対応する場合にのみ意味を持ちます。mockは生成seedを使いません。temperature=0やseedでも将来の実API応答が完全に再現するとは主張しません。

## resume/cacheと再試行

request keyはprovider/model、条件、表現、sample_id、task、prompt version、本文/契約、decodingのSHA-256です。完了した応答を再開時に再送しません。**不正JSONも完了応答**としてキャッシュし、成績を改善するために自動再試行しません。再取得は `--force` のみです。

JSONLは1件ごとにflush/fsyncし、同一出力先は排他ロックします。中断による末尾の未完行だけを切り捨て、改行まで完結した不正行は拒否します。manifest/score JSONは一時ファイルから置換します。外部呼出し成功直後・保存直前に強制終了した場合の二重課金までは防げません（provider側idempotencyなしでexactly-onceは保証しません）。

`TransientProviderError` だけを標準最大2回（API上限3回）試行します。間隔は標準1秒。誤分類・不正JSON・恒久障害・未知例外は自動再試行しません。各試行のカテゴリを保存します。再試行を使い切った通信障害には応答がなく、採点では欠落として不正扱いです。そのような未完了項目は次回再開時の対象になります。provider_attempt_countは開始した試行数で、課金対象数の保証ではありません。

## 採点・比較表・使用量

Phase 5Bの厳密な検証と採点を再利用します。accuracy、macro_f1_present_classes、診断用macro_f1_all_classes、invalid数、全6ラベルの混同行列、クラス別support/precision/recall/F1を設定別に保存します。欠落・不正応答も分母に含み、F1では偽陰性です。

日本語表と同じ選択窓に対するB−A、C−B、D−Cの記述的差分を出力します。統計的有意差検定・因果的主張はしません。使用量は提供された値だけを合計し、未提供ならnullです。使用量が一部応答だけにある場合はその部分和であり、全コストの推定ではありません。価格を埋め込まずcostはnull（取得不可）とします。

dry-runにもscore形式を生成しますが、未取得の応答による配線確認値です。モデル性能として使わないでください。mock結果も同様です。

## 出力と外部応答取り込み

- `experiment_manifest.json`: 評価者専用。正解対応と選択IDを含む。モデルに渡さない。
- `requests.jsonl`: request_key、評価sample_id、条件、表現、request。**外側を送信せず、requestだけを使用**。
- `responses.jsonl`: raw_response、valid、parsed_result、attempt数、カテゴリ、usage、時刻。
- `failures.jsonl`: 不正応答と試行障害。成功例だけに絞らない。
- `scores.json` / `scores_ja.md`: 設定別結果と記述的差分。
- `predictions/` / `confusion_matrices/`: 設定別JSON。

外部応答JSONLは `{"request_key":"requests.jsonlのキー", "raw_response":"モデルのJSON本文", "usage":{"input_tokens":123,"output_tokens":9}}` の形式です。usageは省略可。未知・重複キーは拒否し、不正なモデルJSON本文は修復しません。欠落した外部応答には通信せず失敗を記録します。同一provider/model・実験設定のdry-run後、以下のexternal指定で取り込んでください。

## 隔離ビルド・テスト・dry-run

リポジトリルートから実行します。実験出力はGit外です。

```bash
source /opt/ros/humble/setup.bash
colcon --log-base /tmp/log_phase5c_llm build --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/build_phase5c_llm --install-base /tmp/install_phase5c_llm
source /tmp/install_phase5c_llm/setup.bash
colcon --log-base /tmp/log_phase5c_llm test --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/build_phase5c_llm --install-base /tmp/install_phase5c_llm --packages-select ros4hc_synthetic_scenario
colcon test-result --test-result-base /tmp/build_phase5c_llm --verbose
ros2 run ros4hc_synthetic_scenario run_llm_experiment --input-dir /tmp/ros4hc_sensor_language_phase5b --output /tmp/ros4hc_phase5c_llm --provider mock --model mock-constant-v1 --mode pilot --dry-run
```

外部実行用の準備例（model/providerは実際の識別子に置換）:

```bash
ros2 run ros4hc_synthetic_scenario run_llm_experiment --input-dir /tmp/ros4hc_sensor_language_phase5b --output /tmp/phase5c_external --provider external --external-provider provider-id --model exact-model-id --dry-run
ros2 run ros4hc_synthetic_scenario run_llm_experiment --input-dir /tmp/ros4hc_sensor_language_phase5b --output /tmp/phase5c_external --provider external --external-provider provider-id --model exact-model-id --responses-file /tmp/external_responses.jsonl
```

全自動試験はmockを使用し、実APIには依存しません。実LLMでの接続確認・pilotは安全な設定が準備できてから別途実施します。今回のdry-runは実LLM研究結果ではありません。

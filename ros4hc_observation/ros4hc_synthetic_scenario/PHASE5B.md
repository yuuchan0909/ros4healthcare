# Phase 5B — Sensor-to-Language 入力・評価フレームワーク

Phase 5Aの既存出力を消費し、入力条件だけを制御する研究用フレームワークです。Phase 5Aの生成処理、正解、production Observationコードは変更しません。LLM、ネットワーク、APIキー、RAG、文献検索、fine-tuning、HumanStateHypothesis、医学的診断・意思決定は含みません。

## アーキテクチャ

- `sensor_language.py`: Phase 5A出力の読込、入力投影、相対履歴、決定的日本語formatter、漏洩監査、JSONL出力、CLI、プロンプト・JSON Schema出力契約。
- `scoring.py`: 分類・説明応答の構造検査、分類の決定的採点。
- `test/test_sensor_language.py`: 条件差、欠測、意味的漏洩、出力と正解の分離、採点、正規データ非変更。

Python標準ライブラリを使用します。Phase 5Aの内部APIを再利用しますが、既存Observationパッケージに依存しません。

## 条件の正確な差

| 条件 | 現在窓の値 | Observation風メタデータ | 相対履歴 | 絶対時刻 |
|---|---|---|---|---|
| A | 前窓差・傾向を含む現在窓要約 | なし | 明示的履歴列なし | なし |
| B | Aと同じ要約 | あり | 明示的履歴列なし | なし |
| C | あり | あり | 過去6窓まで | なし |
| D | あり | あり | 過去6窓まで | 各窓の開始・終了 |

現在窓の値は `mean_hr_bpm / min_hr_bpm / max_hr_bpm / hr_change_bpm`、利用可能なら `hr_std_bpm`、および `activity_magnitude_mean / activity_magnitude_std / trend` です。HR変化とtrendは前窓に対する計算済みの局所変化特徴であり、A/Bにも時間情報があります。Aはその特徴を含む現在窓要約、Bは同じ要約に自己記述メタデータを追加した条件です。CはBに過去最大30分の明示的な相対履歴列を追加し、DはCに絶対時刻を追加します。研究上の比較は「局所変化特徴を含む単一窓要約 対 明示的な複数窓文脈」であり、「時間情報なし 対 あり」ではありません。値を丸め直したり、欠測を補完したりしません。

B以降のメタデータは `observation_type / unit / unit_status / data_level / availability / quality / source` に限定します。Phase 5Aの `time_semantics` はA/B/Cにコピーしません。生成元・合成単位・品質は入力の意味を明示し、対象クラスを表すものではありません。

C/Dの窓は過去→現在の順で、`relative_minutes` は -30, -25, …, 0。`window_minutes=5`、`available_history_windows / available_history_minutes` で実際に利用できる履歴量を明示します。日初は現在窓のみ、次窓は過去1窓と現在窓です。相対履歴は5分窓単位で、未来の窓を含みません。

**Condition D may exploit time-of-day patterns and must not be interpreted as sensor-only state inference.**

Condition Dは時刻パターンを利用でき、センサのみの状態推論と解釈してはいけません。

## 入力境界とGround Truth分離

JSONLの外側レコードは次の構造です。

```json
{"sample_id":"状態を含まない不透明な24桁hex ID", "condition":"a", "task_input":{}}
```

将来モデルへ送信するのは **`task_input` のみ** です。外側レコード全体を送信しないでください。`sample_id` は窓IDと摂動variantから生成するSHA-256の先頭24桁で、状態を符号化しません。同一窓・variantでは全条件・表現に共通します。条件・表現をまたいで重複利用するため、採点は条件・表現・タスクごとに別々に行ってください。異なるシナリオ間でのグローバルな一意性は保証しません。

`task_input` には窓ID・対象者ID・index・scenario名・正解・活動カテゴリを入れません。D以外には絶対時刻を入れません。数値・事実を持つキーを許可リストで投影し、禁止英日ラベル・同義語の監査を構造化入力と日本語入力に実施します。内部に追加された `activity_level` なども投影しません。`trend` の増加・減少・閾値以内は数値差に基づく観測事実です。

`evaluation_ground_truth.json` は評価者専用です。sample_idと窓ID、期待ラベル、元のシナリオラベル、variant、摂動詳細を保持します。モデルに送らないでください。日本語要約・設定・Phase 5A正解もAI入力ではありません。

## 2つの将来タスクとプロンプト

生成する `classification_prompt_ja.txt` と `explanation_prompt_ja.txt` は日本語テンプレートです。`{task_input}` に構造化JSONのシリアライズ結果または日本語事実記述を代入します。同じ入力を2つのタスクに使えるためJSONLはタスク別に重複生成しません。

分類は「観測事実のみ」「生活行動を推測しない」「疾病を診断しない」「情報不足なら判断不能」「JSONで英語識別子1つ」を要求します。説明はさらに `evidence / uncertainty / unsupported_inferences_avoided` を要求し、文献引用を禁止します。

分類・説明の両プロンプト本文に、全6種類の英語状態IDと日本語表示名を常に明記します（方式A）。別ファイルの存在を知らないモデルにも回答候補が伝わります。同じ候補を `classification_output_schema.json` / `explanation_output_schema.json` にも定義します。回答候補一覧はサンプルによらず一定で、正解を指定しません。これは分類タスクの定義であり、ターゲット漏洩ではありません。タスク指示・回答候補と、ラベル語彙を禁止する観測本文 `task_input` の境界を区別し、漏洩監査は観測本文に適用します。将来の呼出しでは生成されたテンプレート全体を使用してください。

```python
import json
from ros4hc_synthetic_scenario.sensor_language import CLASSIFICATION_PROMPT
# recordはJSONLの1行。sample_idやconditionを含めない。
content = record['task_input']
text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
prompt = CLASSIFICATION_PROMPT.format(task_input=text)
# prompt自体に全6候補が含まれる。出力契約JSONは構造検査にも使用できる。
# ここではモデルに送信しない。
```

## 構造化表現と日本語Sensor Facts

両者は同一の投影済み入力から生成します。formatterはAIではなく決定的テンプレートです。数値を追加で丸めず、HR要約、活動量、前窓差、欠測、全メタデータ、利用可能履歴量とDの時刻を保持します。品質情報などの機械enum/JSON値は日本語文に併記します。

欠測HRは「心拍データは利用できない。」、欠測活動量は「活動量データは利用できない。」と表します。Aでは欠測モダリティは空オブジェクトで、availability等のメタデータを追加しません。B以降では欠測メタデータも残します。未知単位は「単位の確度は不明である。」と明示します。`*_bpm` はPhase 5Aフィールド名の単位を維持しますが、unknown摂動時の確度が検証済みであるとは主張しません。

「運動中」「歩行している」「回復中」などの解釈を事実記述に追加しません。

## 摂動

`--include-perturbations` で清浄288件に加え、各7種類×288件を追加します。各条件・各表現は2304件になります。摂動名はJSONLの観測本文にも外側にも含めず、評価ファイルにのみ保存します。

各摂動サンプルでは**当該窓だけ**を変更し、履歴は元データです。日全体の故障を模擬するものではありません。Phase 5Aのコピー型APIを当該窓に適用し、元データは変更しません。欠測や異常を相互に重ねる新しい実験条件は設計しません。

`missing_both` はPhase 5Aが明示した期待値 `indeterminate` を評価側で使用し、元のシナリオラベルも保存します。履歴があれば推測可能な場合があっても、Phase 5Bで期待値を再定義しません。他の摂動は元のラベルを保持します。既に摂動済みの入力ディレクトリでは `perturbations.json` も読み込み、既存の期待値を保持します。標準実験では清浄なPhase 5Aディレクトリに追加フラグを使ってください。

## 採点API

```python
from ros4hc_synthetic_scenario.scoring import score_classification, validate_response
expected = {row['sample_id']: row['ground_truth_state'] for row in evaluator_rows}
# 応答はJSON文字列またはdict。以下はAPIの形を示すだけで、モデルは呼び出さない。
metrics = score_classification(expected, predictions_by_sample_id)
valid = validate_response(explanation_response, task='explanation')
```

分類の正解率、各クラスのsupport/予測数/TP/precision/recall/F1、混同行列、2種類のmacro-F1、不正応答数を返します。混同行列は行が正解、列が予測です。欠落と不正応答は正解率の分母に含め、不正応答数をクラス別にも記録します。不正応答は混同行列の6列には入れず、F1計算では偽陰性になります。**主指標 `macro_f1_present_classes` は正解support>0のクラスのみのF1等重み平均**です。診断用 `macro_f1_all_classes` は固定6クラスの平均です。分母ゼロのクラスF1は0、正解クラスが1つもない場合の両平均も0と定義します。清浄日の5クラスを完全に予測すると主指標は1、全クラス平均は5/6です。indeterminateも正解support>0なら主指標に含めます。混同行列は引き続き全6ラベルを保持します。曖昧な旧キー `macro_f1` は返さず、2つの明示的キーを使用します。

未知のsample_idは対応ミスとして拒否します。JSONフェンス、余剰キー、重複キー、無効ラベルを修復しません。説明は必須キー、正しいラベル、空でないevidence文字列配列、空白だけでないuncertainty、避けた推測の文字列配列を検査します。根拠の意味的正しさ、文献引用の有無、文章の質を自動採点するものではありません。LLM-as-a-judgeは実装しません。

## 隔離ビルド・テスト・実生成

リポジトリルートで実行します。既存build/installディレクトリは使用しません。

```bash
source /opt/ros/humble/setup.bash
colcon --log-base /tmp/log_sensor_language build --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/build_sensor_language --install-base /tmp/install_sensor_language
source /tmp/install_sensor_language/setup.bash
colcon --log-base /tmp/log_sensor_language test --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/build_sensor_language --install-base /tmp/install_sensor_language --packages-select ros4hc_synthetic_scenario
colcon test-result --test-result-base /tmp/build_sensor_language --verbose
ros2 run ros4hc_synthetic_scenario generate_sensor_language_inputs --scenario-dir /tmp/ros4hc_synthetic_scenario_subject001 --output /tmp/ros4hc_sensor_language_phase5b
ros2 run ros4hc_synthetic_scenario generate_sensor_language_inputs --scenario-dir /tmp/ros4hc_synthetic_scenario_subject001 --output /tmp/ros4hc_sensor_language_phase5b_perturbations --include-perturbations
```

`--history-windows` は標準6。既存Phase 5A生成日がなければREADMEの `generate_daily_scenario` で先に生成してください。CLIは日を自動再生成せず、入力ディレクトリのデータをそのまま使用します。

出力は `condition_{a,b,c,d}_{structured,ja}.jsonl` の8ファイル、プロンプト2ファイル、出力契約2ファイル、評価用正解1ファイル、日本語要約1ファイルです。モデルは一度も呼び出しません。

## 実験上の制限

現在の日程は固定です。Dは絶対時刻の近道を利用でき、連続窓は統計的に独立ではありません。数値範囲は意図的に分離されており、このデータセットはパイプライン検証用です。最終的な一般化性能や生理学的・臨床的妥当性を主張できません。hr_changeやtrend自体にも前窓情報があるため、A/Bも完全に窓内の情報だけではありません。

将来は複数日程、イベント時刻のランダム化、重なるセンサ値分布、複数対象者、日または対象者単位の評価分割が必要です。これらはPhase 5Bには実装しません。

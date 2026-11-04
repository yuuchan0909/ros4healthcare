# ros4hc_synthetic_scenario — Phase 5A

このpackageは、Sensor-to-Language評価のために単一人物の24時間を再現可能に生成する独立した研究用fixtureです。24時間÷5分 = **288窓**。標準対象者は `subject_001`、合成時刻は2026-01-01 00:00 UTCから翌日00:00 UTCです。実測、生理学モデル、臨床的に検証されたモデルではなく、生理学的・臨床的妥当性を主張しません。

## 構成と依存

Python標準ライブラリとament_pythonのみを使用します（テストはpytest）。Observationパッケージへの依存や変更はありません。

- `configs/daily_subject_001.json`: 日本語説明付き設定。seed・対象者・窓長・スケジュール・HR/活動量パラメータ。
- `dataset.py`: 設定検証、生成、データ検証、コピーによる摂動、履歴取得、出力。
- `cli.py`: ネットワーク、実センサ、BLE、LLM、APIキー不要のCLI。
- `test/test_dataset.py`: 境界、再現性、出力分離、摂動、不正入力検証。

## 活動シナリオと評価ラベル

| 時刻 (UTC) | 隠された評価ラベル |
|---|---|
| 00:00–07:30 | low_activity / 低活動状態 |
| 07:30–08:30 | light_activity / 軽活動状態 |
| 08:30–09:00 | walking_like_activity / 歩行様活動 |
| 09:00–12:00 | low_activity / 低活動状態 |
| 12:00–13:00 | light_activity / 軽活動状態 |
| 13:00–16:30 | low_activity / 低活動状態 |
| 16:30–17:00 | walking_like_activity / 歩行様活動 |
| 17:00–17:30 | high_activity / 高活動状態 |
| 17:30–18:00 | post_activity_recovery / 活動後回復状態 |
| 18:00–20:00 | light_activity / 軽活動状態 |
| 20:00–22:00 | low_activity / 低活動状態 |
| 22:00–23:30 | light_activity / 軽活動状態 |
| 23:30–24:00 | low_activity / 低活動状態 |

`indeterminate / 判断不能` は欠測評価用です。標準の清浄データには含めません。
ラベルはHR・活動量と時間変化から比較的観測可能な範囲に限定します。歩行様は実際の歩行の証明ではありません。睡眠、食事、仕事、疲労診断、疾病ラベルを付けません。HRのみから状態を一意に識別できるとは主張しません。

## 合成特徴量

HR平均は状態別基準値 (60/76/96/138 bpm) にseedで制御した一様変動 ±2 bpmを加えます。最小・最大は平均±4 bpmを40–180 bpmに制限します。これは窓内の実サンプルから計算した統計ではなく、構成した要約値です。回復区間は112 bpmから窓ごとに7 bpm低下し、77 bpmで終わります。`hr_change_bpm` は**直前窓の平均との差**で、初窓はnullです。回復値には乱数を加えません。

活動量平均は低活動0.04、軽活動0.2、歩行様0.65、高活動1.3、回復0.08に一様変動±0.015を加え、0–2の範囲に制限します。標準偏差は平均×0.2です。物理的なm/s²やgではなく、明示的な合成単位 `synthetic_activity_unit` です。200 Hzのベクトル信号や実際の加速度演算を再現しません。`trend` は直前窓との差が変動幅の2倍を超える場合に増加/減少、それ以外はstable、初窓はunavailableです。状態名に相当する `activity_level` はAI入力に含めません。

範囲・変動幅・回復値・活動量比率は設定で変更できます。標準窓長は5分、期間は1440分に限定して検証します。

## Observationとの概念対応

独立JSONスキーマです。ROSメッセージそのものではありません。

| JSON情報 | Observationの対応概念 |
|---|---|
| subject_id, window_id | 対象者、記録識別子 |
| start_time/end_time | 合成窓区間（UTC、TIME_SYNTHETIC） |
| metadata.observation_type | hr または activity（本fixture内の型識別子） |
| metadata.unit/unit_status | bpm または合成単位、UNIT_STATUS_VERIFIED |
| metadata.source | synthetic_hr_generator / synthetic_activity_generator |
| metadata.data_level | DATA_SYNTHETIC_SAMPLE |
| metadata.quality | VALIDITY_VALIDは構造的有効性のみ。品質スコアは未提供 |
| metadata.availability | available / missing |

VERIFIEDは生成器が単位を明示している意味で、実測検証ではありません。数値品質スコアはnull、scaleは空、quality_availableはfalseです。実在Polar H10などの機器、測定時刻、topic、センサ品質を捏造しません。VectorObservationへの直接変換は行いません。

## Ground Truthの分離と出力

- `sensor_windows.json`: **AI入力**。窓ID、時刻、前窓ID、位置、HR・活動特徴量とメタデータのみ。
- `daily_windows.csv`: **AI入力に使用可能**。日本語ヘッダー、UTF-8 BOM。Ground Truthなし。
- `ground_truth.json`: **評価者専用**。窓IDと英語ラベル、日本語ラベル。
- `scenario_summary_ja.md`: **評価者専用**。状態別窓数を含む日本語要約。
- `perturbations.json`: 摂動を適用してexportした場合のみ。**評価者専用**。

出力フォルダ全体、設定、README、評価用ファイルをAIに渡さないでください。コード中の状態名・日本語ラベルがAI入力JSON/CSVに存在しないことを自動テストします。時刻や数値と状態の統計的関係は意図した評価信号であり、匿名化を保証するものではありません。

## 時間文脈と摂動API

```python
from ros4hc_synthetic_scenario.dataset import load_config, generate, temporal_context, perturb, export
config = load_config('configs/daily_subject_001.json')
day = generate(config)
current = temporal_context(day['sensor_windows'], 210, previous=0)
with_previous = temporal_context(day['sensor_windows'], 210, previous=1)
with_30min = temporal_context(day['sensor_windows'], 210, previous=6)
changed = perturb(day, 'missing_both', 210)
export(changed, '/tmp/scenario_missing_both')
```

履歴は当該窓を含み最大7窓です。日初は利用可能な履歴のみを返します。コピーを返し、元データを変更しません。

| 摂動名 | 処理 |
|---|---|
| missing_hr / missing_acc / missing_both | 指定モダリティの値を除去。availability=missing、VALIDITY_MISSING |
| unknown_unit_metadata | 両モダリティの単位を空にしUNIT_STATUS_UNKNOWN |
| hr_anomaly | HR最小170・平均175・最大180 bpmの注入 |
| acc_anomaly | 活動量平均20、標準偏差10という範囲外値の注入 |
| sensor_contradiction | 高HRと低活動量0.01の同時注入 |

これらは頑健性評価用の固定値で、生理的異常や診断を意味しません。HR変更時の平均差はnull、活動量変更時のtrendはunavailableとし、周辺窓は変更しません。清浄データ用validate_datasetは摂動データ用の検証器ではありません。摂動仕様・missing_bothの期待ラベルindeterminateは評価側にのみ記録し、元の活動Ground Truthは保持します。状態推論は実行しません。

## ビルド・テスト・生成

リポジトリルートから実行します。既存build/installを変更しないよう専用ディレクトリを/tmpに作成します。

```bash
source /opt/ros/humble/setup.bash
colcon --log-base /tmp/log_synthetic_scenario build --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/build_synthetic_scenario --install-base /tmp/install_synthetic_scenario
source /tmp/install_synthetic_scenario/setup.bash
colcon --log-base /tmp/log_synthetic_scenario test --base-paths ros4hc_observation/ros4hc_synthetic_scenario --build-base /tmp/build_synthetic_scenario --install-base /tmp/install_synthetic_scenario --packages-select ros4hc_synthetic_scenario
colcon test-result --test-result-base /tmp/build_synthetic_scenario --verbose
ros2 run ros4hc_synthetic_scenario generate_daily_scenario --output /tmp/ros4hc_synthetic_scenario_subject001
```

`--config` でJSON設定、`--seed` でseedを上書きできます。ROSなしでも `PYTHONPATH=ros4hc_observation/ros4hc_synthetic_scenario python3 -m ros4hc_synthetic_scenario.cli --output /tmp/ros4hc_synthetic_scenario_subject001` で生成できます。

## 限界と次段階

単一人物・単一日・固定スケジュールで、実世界の個人差、センサノイズ、活動の多義性、窓内周期性をモデル化していません。高精度な分類が得られても実測データへの一般化は示せません。将来はcurrentのみと30分履歴のSensor-to-Language評価、文献grounding / RAG比較に用いる予定です。Phase 5AにはLLM、RAG、文献検索、HumanStateHypothesis、医学的診断を実装しません。

## Phase 5A 最終ターゲット漏洩監査

AI入力JSONの全階層とCSVの全列を対象に、状態・シナリオ・活動クラス等の禁止キー、英日ラベルと同義語（low / high / walking / recovery / 安静など）を検査します。JSONキー、CSVヘッダー、活動傾向の許可集合をテストで固定し、全7摂動の出力にも適用します。禁止キーや同義語を意図的に注入した負例も検出します。`activity_level` はAI入力にありません。`trend` は数値差から求める事実記述です。生成元・単位・品質・利用可否のカテゴリは対象状態に応じて変更されません。

固定日程では時刻・窓位置と正解が相関します。また状態別数値範囲が分離した簡単なfixtureです。明示的ラベル漏洩がなくても、同じ日程の窓を学習・評価に分割すると分類性能を過大評価し得ます。将来の比較実験では時刻や識別子を分類器に渡す範囲を定義し、日程を変えた評価日などを別途設計してください。本監査では時間文脈要件と既存生成値を維持します。

## Phase 5B — Sensor-to-Language入力・評価準備

A〜Dの入力条件、構造化/日本語事実表現、プロンプト・出力契約、摂動入力と決定的採点を追加しました。外部LLMは呼び出しません。実行方法・入力境界・実験上の制限は [PHASE5B.md](PHASE5B.md) を参照してください。

Phase 5BのAは前窓差 `hr_change_bpm` と `trend` を含む現在窓要約（明示的履歴列・メタデータなし）、Bは同じ要約＋自己記述メタデータ、CはB＋過去最大30分の明示的相対履歴列、DはC＋絶対時刻です。比較は「局所変化特徴を含む単一窓要約 対 明示的な複数窓文脈」であり、「時間情報なし 対 あり」ではありません。

分類の主要macro-F1は正解support>0のクラスのみを平均する `macro_f1_present_classes` です。全6クラス平均 `macro_f1_all_classes` は診断用に併記します。分類・説明プロンプトは全6候補の英語ID・日本語表示を本文に明記し、観測本文 `task_input` の漏洩監査とは分離しています。

## Phase 5C — LLM分類実験runner

provider非依存のrequest構築、52窓の対応付きpilot、dry-run、逐次保存・再開、限定再試行、外部応答取り込み、設定別採点を追加しました。実API設定がないためlive推論は行っていません。mock/dry-runの値をLLM性能と扱わないでください。詳細は [PHASE5C.md](PHASE5C.md) を参照してください。

## Phase 5D: 未来予測用の軌跡

モデル非依存の観測履歴・未来ターゲット分離、adapter契約、持続予測baselineを追加しました。World Model本体の実装・学習はありません。定義、CLI、時間漏洩と1日fixtureの制約は [PHASE5D.md](PHASE5D.md) を参照してください。

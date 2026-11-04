# Phase 5C Astra clean pilot — 2026-11-04

## 1. 実験目的

Phase 5B Sensor Factsの情報条件差をgpt-6-astraで予備評価する。synthetic fixture上の状態解釈の予備実験であり、臨床性能・転倒予測性能を評価するものではない。

Aは前窓差・trendを含む現在窓要約、BはA＋自己記述メタデータ、CはB＋過去最大30分の明示的相対履歴、DはC＋絶対時刻である。A/Bにも局所変化という時間情報がある。structuredとjaは同じ事実の表現形式の比較である。

## 2. 固定条件

| 項目 | 設定 |
|---|---|
| provider | openai |
| model | gpt-6-astra |
| reasoning_effort | medium |
| reasoning mode | API既定のstandard。runnerに新機能は追加していない |
| temperature | 未指定（API default、payloadに含めない） |
| provider seed | 未指定 |
| max_output_tokens | 未指定 |
| mode | pilot |
| dataset | clean |
| conditions | a,b,c,d |
| representations | structured,ja |
| sampling seed | 42 |
| 対象 | 52 samples × 8 settings = 416 requests |
| git commit | `a100fd0330f64fe4949c31bdce79e8cf2342ea7c` |

全設定で同じ52窓を用いた。dry-run manifestの指定条件・prompt hash・source hashesを確認してから、別ディレクトリでlive pilotを実行した。live manifestとの設定・入力ハッシュ一致も確認した。`store=false`で、web searchその他のtoolsはモデルに与えていない。

## 3. 実行監査

| 項目 | 件数 |
|---|---:|
| completed | 416 / 416 |
| provider attempts | 417 |
| transient failure | 1 |
| retry | 1 |
| permanent failure | 0 |
| invalid | 0 |
| missing | 0 |

transient通信障害のみ既存runnerの規則で再試行した。不正応答を都合よく再生成する操作や実験条件の変更は行っていない。provider attemptsは呼出し試行数であり、課金対象数を保証しない。

## 4. 結果

各設定の評価対象は52窓。Macro-F1は既存の`macro_f1_present_classes`（正解supportがあるクラスの平均）である。

| 条件 | 表現 | Accuracy | Macro-F1 |
|---|---|---:|---:|
| A | structured | 84.62% | 0.7575 |
| A | ja | 80.77% | 0.7454 |
| B | structured | 84.62% | 0.8261 |
| B | ja | 73.08% | 0.7711 |
| C | structured | 98.08% | 0.9782 |
| C | ja | 100.00% | 1.0000 |
| D | structured | 100.00% | 1.0000 |
| D | ja | 100.00% | 1.0000 |

## 5. Paired descriptive differences

同一窓に対応する記述的差分。Accuracy差の単位はパーセントポイント（pt）、Macro-F1差はスコア差である。

| 差分 | 表現 | Accuracy差 | Macro-F1差 |
|---|---|---:|---:|
| B−A | structured | 0.00pt | +0.0686 |
| B−A | ja | −7.69pt | +0.0257 |
| C−B | structured | +13.46pt | +0.1521 |
| C−B | ja | +26.92pt | +0.2289 |
| D−C | structured | +1.92pt | +0.0218 |
| D−C | ja | 0.00pt | 0.0000 |

## 6. クラス別の重要な所見

各設定に共通する正解supportは以下のとおり。

| クラス | support |
|---|---:|
| low_activity | 16 |
| light_activity | 16 |
| walking_like_activity | 8 |
| high_activity | 6 |
| post_activity_recovery | 6 |
| indeterminate | 0 |

post_activity_recoveryのRecallは次のとおり。

| 設定 | Recall |
|---|---:|
| B structured | 2/6 |
| B ja | 3/6 |
| C structured | 6/6 |
| C ja | 6/6 |

B-jaではindeterminate予測が3件あった。indeterminateは正式な6候補の1つとして採点対象に含めた。正解supportが0なので主要Macro-F1の平均対象には入らないが、その予測による誤分類はAccuracyと実際の正解クラスのRecall/F1に反映される。全6クラスのsupport・Precision・Recall・F1と混同行列はGit外の実験結果に保存している。

## 7. Token usage

| 項目 | tokens |
|---|---:|
| input_tokens | 614118 |
| output_tokens | 20550 |
| total_tokens | 634668 |

416応答から取得できたusageの合計である。transient失敗試行の未取得usageは含まれない。

## 8. Reproducibility

実験コード・標準設定の基準commitは`a100fd0330f64fe4949c31bdce79e8cf2342ea7c`。Phase 5A/5Bはこのcommitの正式CLIと標準設定から再生成し、test fixtureは流用していない。Phase 5Aのseed上書きは行わず、設定内の42を使用した。Phase 5Aはsensor_windows・ground_truth各288件、perturbations.jsonなし。Phase 5Bはhistory-windows=6、include-perturbations未指定で、evaluation ground truthと全8設定が各288件、sample_id集合一致、cleanのみを確認した。

永続保存先（Git外）:

- Phase 5A: `~/ros4hc_experiments/inputs/phase5a_subject001_a100fd0`
- Phase 5B clean: `~/ros4hc_experiments/inputs/phase5b_clean_a100fd0`
- dry-run: `~/ros4hc_experiments/phase5c/astra_medium_pilot_20261104_dry`
- live結果: `~/ros4hc_experiments/phase5c/astra_medium_pilot_20261104`
- 全入力SHA-256記録: `~/ros4hc_experiments/inputs/phase5ab_a100fd0_sha256.json`

主要SHA-256:

| ファイル | SHA-256 |
|---|---|
| `configs/daily_subject_001.json` | `f14476312cd80a892c252281f1ff11e4804be71dc959a4add4d5ef09b76f0028` |
| Phase 5A `sensor_windows.json` | `ce5ec178cc34afaac0b2a151daf73584e8ded24c4a038f298617bc06291e2cd4` |
| Phase 5A `ground_truth.json` | `821b66c96606de024d90586c3a1c796c05ae7717a6d0c160965177dd7b5e58eb` |
| Phase 5B `evaluation_ground_truth.json` | `90897398ceee0d7aaffe3b2ed8e7288e2b9b2691440d1fa9f3865e57aa5a10c2` |

実験manifestにはprompt hash、dataset source hashes、選択sample IDs、decoding、実装モジュールのSHA-256も保存されている。実験後に入力ハッシュと実装ハッシュの一致を確認した。raw responsesとAPI keyはこのGit記録に含めない。

## 9. 解釈

- C−Bでは両表現でAccuracyとMacro-F1が上昇した。明示的履歴が状態解釈を助ける可能性を示すが、このfixture・単回実験での記述的所見に限る。
- B−AではMacro-F1は両表現で上昇した一方、Accuracyはstructuredで同値、jaで低下した。メタデータ追加だけの効果は指標・表現を通じて一貫しない。
- D−Cはabsolute time shortcutを含み得るため、sensor understandingの改善とは解釈しない。
- 統計的有意差・因果的効果・一般化性能・臨床性能は主張しない。

## 10. Limitations

- synthetic fixed schedule: 固定日程の合成データである。
- 52-window pilot: 小標本の予備実験である。
- strong class imbalance: 層化pilotであり、元の1日分布とも異なる。高活動・回復は各6窓、判断不能の正解例は0窓である。
- continuous-window dependence: 連続窓は独立ではなく、明示的履歴も重複する。
- overlapping/easy synthetic distributions: 履歴の重複と、クラスの値範囲を識別しやすい合成分布により、容易な比較になり得る。
- single model run: 1モデル・単回実験であり、生成の揺らぎを評価していない。
- no real sensor validation: 実センサによる検証は行っていない。

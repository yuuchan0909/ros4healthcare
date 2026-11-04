# Phase 5E — 複数subject・複数パターンの合成軌跡

## 目的と範囲

Phase 5Aの1subject・1日・固定日程・容易な数値分離はパイプライン検証用です。Phase 5Eは日程・subjectパラメータ・遷移を変えた研究fixtureを追加します。標準は4subject×7日×288窓＝8064窓、7896軌跡。CPUのみで生成し、学習・LLM呼出しはしません。生理学的実データでも臨床モデルでもありません。World Modelの学習・汎化性能をまだ主張しません。

## subject差と日程

subjectには中立IDとrest_hr（56〜72）、response_scale（0.8〜1.2）、activity_scale（0.8〜1.2）、recovery_rate（0.12〜0.35）、jitter（1〜3）を持たせます。年齢・性別・疾患などは付与しません。プロファイルは評価者専用です。HRは40〜180、ACCは0〜2.5の合成boundsに制限します。

各日は低活動を基底に、軽活動3区間、歩行様2区間、高活動1区間を置きます。開始時刻と継続窓数を許可領域内で乱数化し、歩行様・高活動後に4〜10窓の回復を置きます。最悪ケースの継続時間と回復を含めて重複を拒否します。全288窓を埋め、欠落分や日跨ぎ活動を作りません。状態は既存6クラスを保持しますが、clean日程にindeterminateを置きません。

日HR基準差（-4〜4）、日強度（0.75〜1.25）、区間振幅も変動します。開始時間領域の制約は残るため、完全自由な日程ではありません。同じseed/configで同じ出力、異なるseedで日程・値が変わります。subject数・日数・seed・modeはCLI引数です。

`--config` JSONではprofile_ranges、schedule_ranges、ramp_windows、recovery_windows、day_hr_offset、day_intensityを上書きできます。schedule_rangesの各要素は `[state, 最小開始index, 最大開始index, 最小継続窓数, 最大継続窓数]`。profile範囲は宣言済みbounds内で変更します。日変動HRは-10〜10、強度は0.5〜1.5以内。継続・回復を含む日程重複は拒否します。設定例:

```json
{"ramp_windows":[2,5],"recovery_windows":[4,10],"day_hr_offset":[-4,4],"day_intensity":[0.75,1.25]}
```

## 難易度と遷移

simpleは小さいjitterと離れた目標振幅を使うデバッグ条件です。overlapping（標準）は区間HR応答の揺らぎ±14、ACC倍率0.55〜1.45、増加した観測jitterを使います。light/walking、walking/high、recovery/walkingのHR範囲は重なります。単一閾値で全クラスを同定できる設計ではありません。ただし範囲の重複は分布の同一性や医学的妥当性を意味しません。

目標値に即座に切り替えず、2〜5窓の時定数でHR/ACCを漸近させます。回復HRはsubjectごとの速度で基準に近づきます。隠れた状態ラベルは区間境界で変わる一方、観測には慣性が残ります。観測jitterがあるため、overlappingの回復観測が毎窓単調減少する保証はありません。回復終了は合成日程の注釈であり、実測閾値到達を表しません。

## eventと分離

activity_started/endedは歩行様・高活動区間の開始/終了、recovery_started/completedは回復区間の開始/終了に対応する明示的なsynthetic annotationです。軽活動区間の出入りはこのevent語彙の対象外です。イベントはsensor_windowsから推定しません。

出力はdataset_manifest.json、subjects/subject_NNN/day_NNN/{sensor_windows,ground_truth,events}.json、all_sensor_windows.jsonl、all_ground_truth.jsonl、all_events.jsonl。manifestには設定、subject profile、日程、日付、scenario/day groupを含めるため評価者専用です。IDはsubject/day/窓位置のみでラベルを符号化しません。モデルにはIDや絶対時刻を渡しません。

5DのTrajectorySampleとWorldModelAdapterは変更しません。過去6窓＋現在、+5/+15/+30分の全未来が同日にある場合だけ軌跡化します。各日282件。先頭の履歴は短縮します。action/event/context/interventionは全てknown=false,value=null。正解・未来数値・イベント・遷移・split・scenarioはモデル入力から分離します。許可スキーマによるfail closed監査と注入テストを行います。

## 分割と時間漏洩

標準research splitはsubject_001〜003のday_001〜005をtrain（15日）、同subjectのday_006〜007をvalidation（6日）、subject_004全日をtest（7日）とします。trainは4320窓/4230軌跡、validationは1728窓/1692軌跡、testは2016窓/1974軌跡です。subject数・日数を小さくすると空splitが生じ得ます。単subjectはtestのみになります。小規模設定では研究分割として不十分です。

assign_splitsはleave-subject-out、leave-day-out、scenario-day-groupも提供します。CLIの--split-strategy、--validation-groups、--test-groupsで指定します。day groupは暦日YYYY-MM-DD、scenario groupはmanifestのscenario_idです。split_manifest.jsonに各groupと窓・軌跡・状態・遷移件数を保存します。

subject-dayは完全に1splitへ割り当てます。履歴・未来を含めて日を跨がず、split間で元窓が共有されないことをテストします。ランダム窓分割は重複軌跡の時間漏洩を生むため推奨しません。現在のtestはsubject差の留保であり、未知の生成規則・実世界への汎化試験ではありません。

## 評価と診断

PersistenceBaselineは未学習です。全体とtrain/validation/testでHR/ACCそれぞれのMAE/RMSEをhorizon別、same-state/transition別に算出します。split別結果はデータ特性の記述であり、学習済みWorld Modelの結果ではありません。

dataset_diagnostics.jsonには状態別HR/ACCのcount/mean/母標準偏差/min/max、範囲の重なり幅、event件数、隣接状態遷移types、+5/+15/+30分の6×6遷移行列を保存します。行列は全horizonがある282窓/日に揃えます。same-stateの定義は端点状態の一致であり、途中の状態変化がないことを保証しません。全て評価者専用です。

cleanデータに5A摂動や欠損を自動混入しません。subject-day単位の辞書をAPIから取り出せるため、将来選択した日にだけ摂動・欠損を適用できます。その際は数値ターゲットの欠落扱いとindeterminate正解を別途定義してください。

latent-dynamics型は観測履歴と任意の潜在出力を、event-conditioned型は将来明示的に公開する既知イベント条件を扱えます。現在の既知イベント公開条件は実装していません。adapter契約は任意出力に対応し、アーキテクチャに依存しません。将来実センサの5分要約・単位・品質を同じ観測契約に写像できますが、実測正解・イベント・欠損評価・group設計は別途必要です。

## 実行

```bash
ros2 run ros4hc_synthetic_scenario generate_multiday_scenario --output /tmp/ros4hc_multiday_phase5e --subjects 4 --days 7 --seed 42 --mode overlapping
ros2 run ros4hc_synthetic_scenario generate_multiday_trajectories --dataset-dir /tmp/ros4hc_multiday_phase5e --output /tmp/ros4hc_multiday_phase5e/trajectories --history-windows 6 --horizons 5,15,30
```

代表入力と評価ターゲットはrepresentative_model_inputs.json / representative_evaluator_targets.jsonに分けます。dataset_summary_ja.mdにはsubjectと異なる2日の日程、split別スコアを保存します。生成物はGit外へ出力してください。Phase 5A/B/C/Dとproduction Observationには変更を加えません。

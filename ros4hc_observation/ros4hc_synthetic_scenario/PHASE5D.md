# Phase 5D — モデル非依存の軌跡インターフェース

## 目的と設計保留

未来の観測・状態を予測するためのデータ契約と評価を準備します。現在状態の分類だけではWorld Modelとは呼びません。現段階では状態表現・学習目的・介入データが未確定なので、本体のアーキテクチャを決めません。学習、LLM実験、臨床診断は行いません。

Latent dynamics型は観測履歴から潜在状態と未来観測を、event-conditioned型は状態・event・context・interventionから未来軌跡を扱えます。どちらも将来WorldModelAdapterに接続できます。LLMは後段の言語・知識解釈を担当する別モジュールです。

## Observationとの接続と軌跡

Phase 5Aの事実のみを再利用します。HRはmean/min/max/changeと、存在する場合std。ACCはmagnitude mean/stdとtrend。任意でObservation形式メタデータを保持します。production Observation型・validator・adapterは変更しません。

TrajectorySampleはtrajectory_id、model_input、evaluator_targetを持ちます。trajectory_inputs.jsonlとtrajectory_targets.jsonlは別ファイルです。adapterに渡すのはmodel_inputだけです。IDはsubject・日・現在時刻・任意scenario IDのSHA-256で、状態に依存しません。subject/day/scenarioの分割用情報は評価側です。

historyは過去6窓＋現在窓、最大7窓。history_window_countは現在を含む実数、history_minutesは最古窓と現在窓の間隔、current_indexは履歴内の現在位置です。先頭では利用可能な履歴だけを使います。relative_minutesは-30から0。絶対時刻や元窓IDをモデルに渡しません。現在とは5分集計窓の終了後を意味し、+5/+15/+30分は窓位置t+1/t+3/t+6に対応します。

全horizonの未来窓がある場合だけ採用し、日を跨ぎません。標準288窓では282件、元index 0〜281です。時刻は入力データから読み、固定位置や時刻に状態意味を割り当てません。同subject・同暦日内の欠落・逆順を拒否します。日境界は入力時刻の暦日で判定するため、将来データではtimezoneを統一してください。

## 入力と正解の分離

入力には過去・現在の観測だけを置きます。現在・未来の正解状態、未来数値、遷移区分、scenario名、生活行動記述は入りません。action/event/context/interventionは全て `{"known": false, "value": null}`。隠れたシナリオから行動を推定しません。将来既知条件を扱う場合はschemaを明示的に版上げしてください。

許可キー・非有限値・状態表現・履歴相対時間を検査し、未定義情報はfail closedで拒否します。任意の数値に秘密情報を符号化した汚染を検出できるという主張ではなく、Phase 5Aの信頼された観測を入力源にします。

## adapterと評価

WorldModelAdapter.predict(model_input, horizons_minutes)はPredictionを返します。predicted_observationsはhorizonをキーとする観測辞書、predicted_stateはhorizonをキーとする状態辞書です。latent_state、uncertainty、metadataも任意です。潜在状態だけを返すadapterも可能です。未提供フィールドはNone、欠落は未採点としてcount/missing_countを報告します。

PersistenceBaselineは現在のHR meanとACC magnitude meanを全horizonにコピーする持続予測baselineです。World Modelではなく、学習しません。数値指標はHRとACCそれぞれのMAE/RMSEをhorizon別に報告します。単位を混ぜた合成スコアは作りません。状態を返す場合のみaccuracyと正解supportがあるクラスのmacro-F1を計算します。

評価側のみでstate(t)==state(t+h)をsame-state、それ以外をtransitionとして分けます。horizonごとの件数であり、3horizon分の合計はユニーク軌跡数ではありません。

## 時間漏洩と限界

連続軌跡は履歴と未来窓が重複するため、ランダムな窓単位train/test分割は時間漏洩を生みます。manifestはsubject_id/day_id/scenario_idを分割単位として示します。未指定scenario_idはnullのままです。1日fixtureに学習を行わず、汎化性能を主張しません。

将来は複数subject・複数日・異なるscheduleやイベント時刻を用意し、group単位の分割と境界のpurgeを行ってください。現在APIはsubject/dayでグループ化し、任意scenario_idを評価側に保持します。複数scenarioは別々に生成し、連結時はscenario_idを必ず指定します。未知イベントを補完する機能はありません。

将来adapterを差し替えるにはProtocolを満たすpredictを実装し、evaluate(samples, adapter)に渡します。学習済みモデルへの依存はありません。出力契約、ターゲット、単位、欠落率を確認して比較してください。

## CLI（ネットワーク・GPU・APIキー不要）

```bash
ros2 run ros4hc_synthetic_scenario generate_daily_scenario --output /tmp/ros4hc_phase5d_day
ros2 run ros4hc_synthetic_scenario generate_world_model_trajectories --scenario-dir /tmp/ros4hc_phase5d_day --output /tmp/ros4hc_world_model_phase5d --history-windows 6 --horizons 5,15,30
ros2 run ros4hc_synthetic_scenario evaluate_world_model_baseline --trajectory-dir /tmp/ros4hc_world_model_phase5d --output /tmp/ros4hc_world_model_phase5d
```

生成先はGit外を指定します。trajectory_summary_ja.md、persistence_scores_ja.mdと機械可読JSON/JSONLを出力します。元のPhase 5A/B/Cの生成・採点・実験動作は変更しません。

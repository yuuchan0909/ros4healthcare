# Observation Layerの研究評価（Phase 4A / Phase 4B）

本packageは**評価専用**であり、production Observation codeではない。
研究目的は異種センサのpayloadを一つの数値形式へ統合することではなく、共通の意味metadata、
payload固有構造、UNKNOWN / unavailableを同時に保持できるかを調べることである。
既存schema、mapping、adapter、validator、resolverの意味契約は変更しない。

代表ケースは次のとおり。全入力は合成データであり、実機・BLE・Windows・ネットワークは不要。

| ケース | 使用する既存実装 | 表現 |
| --- | --- | --- |
| Polar HR | `map_polar_hr` → `map_health_observation` | ScalarObservation |
| Polar ACC | `map_polar_acc` | VectorObservation |
| Sensomative | `map_reference` → 同じ`InMemoryReferenceResolver`で復元 | ReferenceObservation |

HRは`hr`の値保持を評価し、入力全フィールドの保持を主張しない。ACCの既存`g` / INFERREDは
そのまま報告し、本評価によって単位を確認済みへ変更しない。Sensomativeの単位、byte→cell対応、
物理配置、校正、device timestampは推定しない。RAW_SAMPLEは未解釈notificationの保持であり、
1 notification = 1 physical sampleという保証ではない。

## Phase 4A：事実の抽出と保持評価

`evaluation.py`はROS importを持たないPython関数で、v0.2 modelとgenerated messageを扱う。

- `metadata_facts`：IDの値と有無、観測種別、単位、primary時刻、出典、data level、qualityを抽出。
- `metadata_completeness`：present / empty / UNKNOWN / unavailable / inconsistent /
  unrecognizedを区別。全項目の充足を要求する点数ではない。
- qualityが利用不可ならNaN値と空scaleはunavailable、それ以外はinconsistent。
  利用可能時は値の存在とscaleの空・非空を報告し、妥当性は既存validatorに委ねる。
- `unknown_semantics`：UNKNOWN、空単位、空frame_id、利用不可qualityを事実として報告。
- `evaluate_scalar` / `evaluate_vector`：有限値の厳密な数値一致、両方NaNの保持、各軸とframe_idを比較。
  Infinityは失敗。`exact`は数値保持でありbit単位一致ではない（符号付きゼロは等しい）。
  `pass`は既存validatorの通過も必要。VALIDのNaN、MISSINGの有限値を修復しない。
- `evaluate_reference`：登録した同一resolverを受け取り、native型と全generated fieldを再帰比較。
  RawNotificationのTime、全payload bytes、長さ、session/sequenceなどを比較する。
- `evaluation_report`：未実施の比較は`evaluated: false`。未実施を成功とみなさない。
- `report_json`：厳密JSON。非有限値は文字列`NaN` / `Infinity` / `-Infinity`で表す。

UNKNOWNとunavailableは正当な意味状態であり、自動的に失敗とはしない。
出典はproducerが記録した情報であり、認証やclock同期を検証するものではない。
primary時刻の抽出を行うが、additional_timesの包括的評価は本Phaseの対象外。

## Phase 4B：定量評価

### 研究目的と方法

`benchmark_cases.py`はgenerated inputの作成と既存mapping APIの呼び出しだけを担当する。
production mappingの意味変換を複製しない。`benchmark.py`が保持・検証・処理時間を集計し、
`benchmark_output.py`が日本語CSV・Markdownと安定した英語キーのJSONを生成する。
依存方向は評価packageから既存packageへの一方向である。

入力件数Nごとに、次の決定的なパターンを使う。UUID、実行時刻、処理時間は非決定的。

- HR：`40 + i % 141`。`hr_quality=0`は入力の値として与えるだけで、測定qualityとは解釈しない。
- ACC：x/yを周期変化、11件ごとにy=NaN、空frameと`synthetic_frame`を交互に使用。
  `synthetic_frame`は合成入力文字列であり、実機frameの推定ではない。
- Raw：payload長13 / 0 / 5 / 32を周期使用し、byteは`(i+j)%256`。native identityとhost時刻も変化。
  合成host時刻はmapping契約の確認用で、実機の時刻精度を示さない。

保持検証はN件を1回実施し、時間計測は別にN件×repeats回実施する。
各caseのwarmup後に`time.perf_counter_ns()`で各mapping呼び出しを計測する。
入力生成、config/resolver作成、外側の検証・resolve・サイズ計測・JSON/CSV/console出力は時間に含めない。
既存mapping内部のUUID生成やvalidatorは含める。Referenceでは登録、deepcopy、内部validator、
ROS message生成を含む。HR/ACCはPython modelを返し、generated message変換はサイズ計測時のみ行う。
したがって計測範囲はcase間で異なり、単純な性能優劣を主張できない。

Reference resolverはwarmupと各repeatで新しく作成し、終了時に解放する。
これは既存resolverを再利用する評価側のlifecycleであり、resolver semanticsの変更ではない。
入力N件と計測値をメモリに保持する。大きなNでは容量に注意し、総メモリ量の評価とはしない。

### 指標と学会用表

| 学会用表 | 内容 | 出力 |
| --- | --- | --- |
| 表1：表現・意味情報の範囲 | 入力型、表現、observation_type、unit/status、主時刻の意味、data level、validity、quality可否、出典 | representation_summary.csv |
| 表2：保持・構造検証結果 | 生成/成功/例外/検証件数、scalar値・XYZ・frame・同一resolver復元・native全field保持件数と率 | preservation_summary.csv |
| 表3：実装処理コスト | 合計・平均・中央値・p95・最小・最大処理時間、算出毎秒件数、N/repeats/warmup | performance_summary.csv |
| 補助表：シリアライズサイズ | 入力generated messageとObservationのROS 2 serialized bytes | serialized_size_summary.csv |

表1は記述結果であり点数ではない。意味チェックは不明validity、利用不可qualityのNaN/空scale、
適用時の空frame、不明単位を集計する。率の分母は生成件数、意味チェックだけは適用件数。
利用不可やUNKNOWNの正当な保持を成立とする。frame保持率は既知・空の両方を含む。
Referenceの登録件数は既存mappingが登録を完了してdescriptorを返した件数。

時間統計は成功呼び出しを全repeatでプールし、失敗件数を別記する。
p95はnearest-rank法：昇順で`ceil(0.95*n)`番目（1始まり）。
毎秒件数は`成功計測件数 * 1e9 / 計測時間合計ns`。総時間0なら未取得。
これは実システムのスループット測定ではない。信頼区間・有意差検定は実施しない。

サイズは`rclpy.serialization.serialize_message`で測り、Python object memoryの推定を使わない。
各caseの先頭入力1件（Rawは13 byte payload）を測定し、入力インデックスとpayload長を記録する。
文字列長・payload長に依存するため、固定サイズやN件平均ではない。API未取得時は未取得と記録する。

**ReferenceObservationのシリアライズサイズには、in-memory resolver側で保持される元のRawNotificationの保存コストは含まれない。**
入力RawNotificationとReferenceObservationは別列で示し、両者の和をメモリ使用量とは呼ばない。

### build・test・実行

Ubuntu 22.04 / ROS 2 Humbleの新しいshellで実行する。既存の`build_observation`、
`install_observation`、`log_observation`は変更しない。package検索rootを明示し、
未追跡`apple_watch_replay`を探索・build対象に含めない。

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon --log-base log_observation_eval build \
  --base-paths src/ros4healthcare/ros4hc_observation \
    src/ros4healthcare/ros4hc_drv/sensomative_raw_bridge \
    src/ros4healthcare/ros4hc_drv/sensomative_raw_msgs \
    src/ros4healthcare/ros4hc_msgs \
  --build-base build_observation_eval \
  --install-base install_observation_eval \
  --packages-up-to ros4hc_observation_eval
source install_observation_eval/setup.bash

colcon --log-base log_observation_eval test \
  --base-paths src/ros4healthcare/ros4hc_observation \
  --build-base build_observation_eval \
  --install-base install_observation_eval \
  --packages-select ros4hc_observation_eval
colcon test-result --test-result-base build_observation_eval --verbose

ros2 run ros4hc_observation_eval observation_eval_benchmark \
  --samples 10000 --repeats 5 --warmup 1000 \
  --output /tmp/ros4hc_observation_eval_phase4b
```

件数を明示して回帰テストを行う場合：

```bash
cd ~/ros2_ws/src/ros4healthcare
python3 -m unittest discover -s ros4hc_observation/ros4hc_observation_eval/test -v
ROS_LOCALHOST_ONLY=1 python3 -m unittest discover -s ros4hc_observation/ros4hc_observation_tools/test -v
ROS_LOCALHOST_ONLY=1 python3 -m unittest discover -s ros4hc_drv/sensomative_raw_bridge/test -v
```

Phase 4AのJSONデモは`ros2 run ros4hc_observation_eval observation_eval_demo`。
Phase 4BのCLIの`--help`は日本語。defaultsは10000件、5反復、1000件warmup。
`--output`は明示指定必須。同名出力は再実行時に上書きするため、結果を残す場合は別directoryを使う。
出力は自動的にGit追加しない。ソース配下ではなく`/tmp`等を指定する。

### 出力ファイルと解釈上の注意

- `benchmark_results.json`：設定、環境（Python/OS/ROS/clock）、方法、全caseの構造化結果。
- `representation_summary.csv` / `preservation_summary.csv` / `performance_summary.csv` /
  `serialized_size_summary.csv`：日本語見出し、UTF-8 BOM付き。技術enumは識別子を併記。
- `academic_summary_ja.md`：表1〜3、補助表、制約を含む日本語の学会用要約。

マッピング処理時間はend-to-end latency、sensor latency、BLE latencyではない。
synthetic結果は生理学的・臨床的妥当性の検証ではない。実機評価は別Phaseで行う。
Reference recoveryは同一process・同一resolver instance内のみ（JSONのscopeは
`same_process_same_resolver_instance_only`、`cross_process_evaluated=false`）。
別subscriberや新しいresolver instanceでは復元できず、永続化・再起動復旧も未評価。
ReferenceObservationのシリアライズサイズにはnative payloadの保存コストを含まない。
総システムメモリ削減を主張しない。

単一runは計測pipelineの動作確認に留まる。CPU負荷、Python/ROS実装、メモリ管理、
UUID生成等に依存し、単一runから科学的結論を導かない。

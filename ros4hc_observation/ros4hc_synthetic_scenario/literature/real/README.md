# 実文献キュレーション原簿

papers.jsonlは論文メタデータ、evidence_chunks.jsonlは条件付き所見を保存する。合成fixtureとは混在させない。このディレクトリはキュレーション用原簿であり、自動的に検索コーパスへ投入されるものではない。

現在はpaper_0001（Jia et al., BMC Geriatrics, 2026; 26:554、DOI: 10.1186/s12877-026-07329-6）1件と、その歩行速度・TUGの2chunk。登録内容は研究者から提供された手動キュレーションに基づく。今回のimportでオンライン取得や独立した原資料の再確認は行っていない。

論文・両chunkの状態はevidence_curatedで、verifiedではない。synthetic_fixture=false。source_location_verified=trueは、研究者が節・表・図の位置を確認したという指定を記録したもので、レコード全体をverifiedに昇格させる意味ではない。PDFページ番号と信頼区間は未提供のためnull。通常のverified-only変換では、この2chunkは検索対象に入らない。

## 解釈時に保持する区別

- chunk_0001のガイドライン閾値は歩行速度 <=0.8 m/s。AUC 0.794、感度0.570、特異度0.864は年齢＋歩行速度モデルのvalidation cohortの性能。
- chunk_0002のガイドライン閾値はTUG >15 s。AUC 0.783、感度0.529、特異度0.818は年齢＋TUGモデルのvalidation cohortの性能。
- これらを固定閾値単独の診断性能として扱わない。この区別はcurator_notesだけでなく、将来の変換で保持されるfall_risk.results.reporting_notesにも記録する。
- 7:3分割は同一募集標本内の分割で、validation cohortは39人。別途募集した外部集団による検証でも、前向き転倒追跡でもない。
- 転倒アウトカムは過去12か月の施設内転倒歴の二値情報。未提供の詳細なイベント定義や個人の将来転倒確率は補わない。
- World Guidelinesの原典は今後別のPaperRecordとしてキュレーションする。

## 検証と人手確認

パッケージをbuild/sourceした環境で、リポジトリルートから実行する。

```bash
ros2 run ros4hc_synthetic_scenario validate_real_literature_corpus \
  --papers ros4hc_observation/ros4hc_synthetic_scenario/literature/real/papers.jsonl \
  --evidence ros4hc_observation/ros4hc_synthetic_scenario/literature/real/evidence_chunks.jsonl \
  --mode real \
  --output-report /tmp/ros4hc_phase5g_paper0001/validation_report.json
```

検証の成功は構文・契約への適合を示す。原資料と数値・適用条件・出典の対応を人が確認してから、論文と対象chunkの状態をverifiedへ変更する。それまでは--include-unverifiedを使った結果を研究上のretrieval結果として扱わない。

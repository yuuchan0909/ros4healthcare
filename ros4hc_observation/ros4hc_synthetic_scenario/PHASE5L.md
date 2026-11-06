# Phase 5L: Scholarly Knowledge Acquisition Pipeline MVP

Knowledge acquisition infrastructure。not LLM training、not automatic scientific verification。
machine_extracted != human_verified。OA != unrestricted reuse。
rights are checked per content/location。no publisher scraping。
current MVP is intentionally small。future expansion may use official bulk datasets。
current results do not demonstrate scientific quality ranking。
既存Phase 5H/I/J/K、production Observation、real literature corpusへ接続・変更・自動登録しない。

## Separation of stages

Discovery → per-location rights evidence → identifier normalization → exact-ID deduplication → metadata enrichment →
integrity signals → rights decision → optional API full text → XML identity/permissions check → KnowledgeCandidate保存。
Discovery responseから保存するのはmetadata projectionのみ。abstract / abstractText / abstract_inverted_index /
reference listsはcacheから除き、元responseのSHA256とprojection SHA256を別々に保存する。
知識抽出とhuman verificationはinterface / NotImplementedErrorのみで、実行しない。

- scholarly_models.py: domain-neutral dataclasses / normalized identifiers / license vocabulary / extraction interface
- scholarly_sources.py: official endpoint allowlist / no redirect / rate control / bounded retries / cache / source adapters
- scholarly_acquisition.py: controlled orchestration / dedup / rights / integrity / approved full text / reports

## Sources and official references

- OpenAlex: general discovery、DOI/OpenAlex ID、OA locations、locationごとのlicense、bibliography。
  [API](https://help.openalex.org/api/) / [authentication and limits](https://help.openalex.org/api/authentication/)。
  first pageのみを使い、匿名APIを基本とする。既存OPENALEX_API_KEYがあればAuthorization headerのみで利用し、保存しない。
  アカウント作成・key発行・メール送信は行わない。
- Europe PMC: biomedical discovery、PMID/PMCID、repository OA metadata。
  [REST reference](https://dev.europepmc.org/RestfulWebService)。search resultType=core。
  本文は `https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/fullTextXML` だけ。
- Crossref: DOI lookup、bibliography、license metadata、post-publication updates。
  [REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) /
  [filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/) /
  [retractions and update direction](https://www.crossref.org/blog/retraction-watch-retractions-now-in-the-crossref-api/)。

Google Scholar、publisher website、PDF URL、arbitrary crawling、Sci-Hub、paywall bypass、institutional loginを使わない。
OpenAlex/PDF/Crossref linkがあっても取得しない。HTTP redirectsも追わない。

## Candidate and identity

KnowledgeCandidate: canonical_id、doi/pmid/pmcid/openalex_id、title/authors/year/venue、source_type、
publication_types/topics、access、integrity、acquisition、knowledge_status、quality_signals、provenance、identifier_conflicts。
fall-specific fieldなし。citation countを取り込まず、単一quality scoreを作らない。
quality_signalsはstudy_design / sample_size / population / prospective_or_retrospective /
external_validation / peer_review_status / limitations。未抽出項目はnull / unknown / 空配列。

canonical identityはDOI → PMID → PMCID → OpenAlex ID。
DOIはlowercase・surrounding whitespace除去・doi: / http(s)://doi.org/ prefix除去。
正規化IDの完全一致のconnected componentsでsource/queryをまたいでmergeする。
DOIなしのPMID recordと、DOI+PMIDのbridge recordも同一candidateになる。
同じIDから複数DOI等が検出されたcomponentはconflictを保存しrejectedに隔離する。
title fuzzy matchingはせず、identifierを共有しない重複の可能性は残る。
metadataの優先順位はCrossref → Europe PMC → OpenAlex。異なる観測値はsource別provenanceに残す。
source_typeとpublication_typesはpublication metadataであり、peer reviewや研究品質の認定ではない。

knowledge_status: discovered / metadata_enriched / fulltext_acquired / machine_extracted / human_verified / rejected。
今回の自動処理はdiscovered / metadata_enriched / fulltext_acquired / rejectedだけ。
既存corpusのverifiedとは異なり、human_verifiedを自動生成しない。
KnowledgeExtractionCandidate interfaceのknowledge_typeはempirical_finding / experimental_method /
measurement_method / analysis_method / guideline / review / device_validation / other。
target_concepts/population/measurements/sensors/methods/outcomes/conditions/limitations/provenanceを将来保持できる。

## Rights and full text

自動保存のlicense allowlistはCC BY / CC0（明示されたpublic domain）だけ。
NC、ND、SA、publisher-specific、other、unknown、closedはmetadata_only。
OA宣言やPDF URLの存在だけで権利を推測しない。
Crossref/OpenAlexのlicenseをEurope PMCのdepositへ移し替えない。

Europe PMC locationのPMCID一致、isOpenAccess=Y、同locationの明示的CC BY/CC0 license、
fullTextXML API経路のすべてが揃った場合だけ本文をrequestする。
XML取得後もfront/article-metaのPMCID・DOIとpermissions/licenseの明示URIを検査する。
不一致・不明・制約付き・entity宣言は保存せず、本文をcacheしない。
全宣言がallowlist内でmetadataと一致した場合だけraw XMLとapproved cacheをGit外へ保存する。
source/source identifier/retrieved_at_utc/license/license source/content_sha256/検証結果をfulltext_manifestへ記録する。
rights_reportのcandidate_license_countsは選択locationの表示値であり、paper全version共通licenseを意味しない。
location_license_countsと全location decisionsを併記する。
metadata_onlyは権利/API条件による強制件数。取得上限による未取得はacquisition_report.not_acquiredに含める。

## Integrity signals

OpenAlex is_retractedとEurope PMC Retracted Publicationを出典付きで保存し、positiveを優先する。
Crossrefのupdate-toはnotice → originalの方向なので、notice自体をretractedと誤分類しない。
各DOIについてupdates:{doi} filter（最大5 notices）で、target DOI一致のretraction/withdrawal/correction等を保持する。
明示的retractionがあればrejectedとしてfull text取得対象から外す。
falseは明示的source fieldだけ。未報告のcorrection等はnullで、無事・高品質とは判断しない。
未登録update、providerの不足、最大5 noticesを超える更新、reinstate履歴の完全な解釈は対象外。

## Bounded controlled run

3 queries固定：

1. Parkinson disease wearable sensors
2. gait stability IMU experimental method
3. fall risk older adults wearable sensors

各queryでOpenAlex 10件 + Europe PMC 10件、最大20 discovery observations。
全体最大50 unique candidates。超過分はcanonical ID順で切り、除外IDをdedup_reportへ記録する。
ランキング性能や網羅性の主張はしない。Crossref enrichmentとupdates lookupは保持candidateのDOIごとに各1 request。
full textは成功件数だけでなくattempts自体を最大5件に制限する。再試行はHTTP transport内部で最大2 attempts/request。
通常1.1秒間隔、timeout=20秒、transient/429にexponential backoffとRetry-Afterを使う。
Retry-Afterが30秒超なら早期再送せず、そのrequestをfailedとして残す。
metadataは同一source/path/parametersのcacheを使う。`--cache`指定でrun間共有可能。
cacheはGit外のみ。XMLは権利・identity検査後だけcacheする。
API responseの構造不正・個別record不正・source failureはsanitized errorとして保持する。
credentials・headers・例外本文を保存しない。API keyをURL parameterに入れることも拒否する。

## Execution and validation order

```bash
export PYTHONPATH="$PWD/ros4hc_observation/ros4hc_synthetic_scenario${PYTHONPATH:+:$PYTHONPATH}"
PYTHONDONTWRITEBYTECODE=1 python3 -m ros4hc_synthetic_scenario.scholarly_acquisition \
  --dry-run --output /tmp/ros4hc_phase5l_dry_20261106
PYTHONDONTWRITEBYTECODE=1 python3 -m ros4hc_synthetic_scenario.scholarly_acquisition \
  --output ~/ros4hc_experiments/phase5l/scholarly_acquisition_mvp_20261106
```

focused pytest → package pytest → isolated colcon build → isolated colcon test → git diff --check → dry-run → live。
unit testsはmock/架空fixturesのみ。dry-runも架空の同一paperをsource/query間でmergeするprovider calls=0の確認で、
live discoveryや学術的妥当性の評価ではない。出力はGit外の空directoryのみ、上書き・既存run削除なし。

指定9 artifacts: manifest.json / queries.json / candidates.jsonl / dedup_report.json / rights_report.json /
integrity_report.json / acquisition_report.json / fulltext_manifest.jsonl / report_ja.md。
manifestはcommit/UTC timestamp/query/source/request parameters/counts/dedup/license decisions/
fulltext count/content hashes/artifact hashesを保持する。approved raw XMLはfulltext/、metadata cacheはcache/。
部分的なsource失敗でも他sourceのmetadataを保持する。API供給状況に依存し、同じqueryの将来の検索順位は不変ではない。
固定cacheと正規化規則は再現可能だが、live API全体のsnapshot再現性を保証しない。

## 2026-11-06 pre-live validation

branch=human-state-interpretation、HEAD=a0abf88。
focused pytest 46 passed、package pytest 611 passed、isolated colcon build成功、
colcon test 611 tests / 0 errors / 0 failures / 0 skipped。
git diff --checkと新規ファイル空白検査も成功。
APIなしdry-run `/tmp/ros4hc_phase5l_dry_20261106` はnetwork attempts=0、fulltext=0。
架空source observations 6件を1 candidateへmergeし、unknown rightsをmetadata_onlyとして保持した。
全検証完了後にのみcontrolled liveを実行する。

## 2026-11-06 controlled live result

出力 `~/ros4hc_experiments/phase5l/scholarly_acquisition_mvp_20261106`。
全3 queryでOpenAlex 10 + Europe PMC 10、計60 discovery records。
今回のdiscoveryではexact-ID duplicate=0、unique before cap=60。
canonical ID順で50 candidatesを保持し、10件の除外IDを記録した。
保持candidateのquery内訳: Parkinson 20 / gait stability 17 / fall risk 13。
source観測件数: OpenAlex 30 / Europe PMC 30 / Crossref enrichment 50。
Crossrefは既存50 candidatesへmergeし、新しいpaperとして登録していない。
111 logical requests / 111 network attempts、再試行0、discovery/enrichment/integrity API errors=0。

選択locationのlicense件数: CC-BY 32 / CC-BY-NC 1 / CC-BY-NC-ND 5 /
CC-BY-SA 1 / other 7 / unknown 4（CC0 0）。全location別の宣言とdecisionはrights_reportに保存。
権利/API条件でmetadata_only=34、Europe PMC API metadata gate eligible=16。
fulltext attempts=5、identityとXML licenseの両検査を通過して保存したもの=1。
残り4件はcontent_identity_or_license_not_confirmedとして本文を保存・cacheせず、再取得しなかった。
metadata gate eligibleでも本文の検証成功を保証しない。上限により未試行=11。
実保存ベースのmetadata-only=49（権利/API対象外34 + 本文検証非通過4 + 未試行11）。

取得本文: DOI 10.1371/journal.pone.0356505 / PMCID PMC13505942 / CC-BY。
content SHA256: `896640be242a3ea043a5dffffe10815229af7cf74ab64bf55d658c5c7365b306`。
本文・manifestのcontent hashと全artifact hashを再照合して成功した。

integrity: retracted=true 0、source明示false 26、unknown 24。
correction_present=true 0、unknown 50。未報告は「問題なし」ではない。
knowledge_status: metadata_enriched 49 / fulltext_acquired 1。machine_extracted / human_verifiedは0。
9種類の指定artifacts、metadata cache、承認済みraw XMLをGit外へ保存した。

次のmachine extraction MVPはこの承認済み1本文を入力に設計できるが、今回は抽出・科学的検証をしていない。
4件の本文非通過の詳細原因は保存したsanitized分類より細かく確定していない。
cacheにTTLはなく再利用時も元retrieved_at_utcを保持する。更新・撤回情報の最新性が必要なら新しいcacheで再取得する。
既存corpusへ登録せず、commit / pushも実行していない。

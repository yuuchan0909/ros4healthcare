# Phase 5M: Machine Knowledge Extraction MVP

独立モジュール `python3 -m ros4hc_synthetic_scenario.machine_knowledge`。
入力はPhase 5Lの単一fulltext_manifest.jsonlと、そのmanifestが参照するXMLのみ。
DOI / PMCID / CC BY / SHA256をmanifestとXMLで検証し、不一致時は停止する。

Approved XML → deterministic section/paragraph segmentation → bounded adjacent-section
chunks (18,000 normalized characters, complete paragraphs, maximum 7 initial calls)
→ strict generic schema → local schema / chunk-scoped provenance validation
→ machine_knowledge.jsonl → pending verification_queue.csv。
Referenceはsource_document.json内で分離しLLMへ送らない。table/figure captionsと
XML table textをsource orderで保存する。XML nodeの正規化テキストに対してSHA256を計算する。
source_documentのsource_path/hashとsource_segmentsのparagraph IDで原文へ追跡する。
文字offsetやPDF page番号は生成しない。MathMLと表のレイアウトは完全には保持しない。

knowledge_typeは9種類のallowlist。populationの値は文字列またはnull、他の内容は
文字列配列（不明は[]）。sample_sizeは単位・群を保持する原文由来文字列。
LLMは内容とprovenanceのみを返す。knowledge_id、machine_extracted、human_verified=false、
pendingはコードが設定する。欠落した必須fieldは補完せずrejectする。
同一内容のexact fingerprintが一致した場合はduplicate candidateとして両方を保持する。
言い換えを含む重複の完全検出や意味的entailmentは保証しない。

APIはgpt-6-astra / medium / store=false、tools・temperature指定なし。
各attemptで~/.config/ros4hc/openai.envをsourceし、keyは保存しない。
最大8 attempts、transientのみ各request最大1 retry。content不備は再生成しない。
manifestは各attempt前と応答usage取得直後に保存。途中失敗も部分結果とfailure状態を保存する。
再実行・上書きを拒否する。dry-runはfixtureを構成して全検証・queue生成を通す。
fixtureのfindingsは最初のparagraphの逐語テキストで、抽出性能を評価するものではない。

出力はGit-externalのみ。raw XMLはコピーしない。既存corpusやPhase 5K Connectorへの
自動投入は行わない。今後の接続にはhuman reviewと明示的なschema adapterが必要。
machine extractionであり、学術的正確性を人間が確認したものではない。

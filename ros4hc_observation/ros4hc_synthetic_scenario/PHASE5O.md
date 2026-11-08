# Phase 5O: blind taxonomy stress test

Production Router/Connector/Knowledgeを変更しない探索pilot。
`python3 -m ros4hc_synthetic_scenario.taxonomy_stress_test --output <Git-external-empty-directory>`
で実行する。`--dry-run` は48件の手書き配線fixtureであり評価ではない。
事前に `source ~/.config/ros4hc/openai.env`。資格情報は保存しない。

生成promptは固定domain/styleとID指定のみ。Router定数を生成に使用せず、
独立した漏洩検査で現taxonomy/情報種別/Connector/既存例の文字列一致を検査する。
4 generator + 48 current router + 4 batch auditor = 56 callsを事前記録する。
Routerのみtransient最大1retry、全体60calls上限。gpt-6-astra / medium / store=false、toolsなし。
追加取得や最終回答生成なし。Connector contextは既存build_contextのローカル既定条件。

Auditorは分類の自然さを6カテゴリで判定するがground truthではない。
候補は全audit後に表記一致で集約し、count>1を反復候補として示す。
同義語統合と最終的な改訂判断はhuman reviewと追加壁打ちに委ねる。
catch-all探索閾値は12問以上かつ25%以上。target過多は8超。
review queueは疑わしい分類、候補、判断困難、unknownとの不整合、集中を優先し最大20件。

LLM生成質問＋LLM Auditorによるstress testであり、Task routing accuracyの正式評価ではない。

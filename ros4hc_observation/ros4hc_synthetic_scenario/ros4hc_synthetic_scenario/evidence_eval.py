"""Synthetic fixture engineering diagnostics only, never scientific validation."""
import argparse
import copy
import json
from importlib.resources import files
from pathlib import Path
from .dataset import generate, load_config
from .sensor_language import make_input
from .evidence import build_bundle, generate_query, write_jsonl
from .literature_corpus import load_corpus
from .literature_retrieval import LexicalRetrievalBaseline


def run_demo(output, config):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fixture = files('ros4hc_synthetic_scenario').joinpath('fixtures/synthetic_literature_corpus.jsonl')
    corpus_bytes = fixture.read_bytes()
    (output/'synthetic_literature_corpus.jsonl').write_bytes(corpus_bytes)
    corpus = load_corpus(output/'synthetic_literature_corpus.jsonl')
    rows = generate(load_config(config))['sensor_windows']
    hr = make_input(rows, 0, 'b'); hr['acc'] = {}
    activity = make_input(rows, 204, 'b'); activity['hr'] = {}
    combined = make_input(rows, 204, 'b')
    low = {'hr': {}, 'acc': {}}
    facts = [hr, activity, combined, low]
    names = ['HRのみ', '活動量中心', 'HRと活動量', '低情報入力']
    baseline = LexicalRetrievalBaseline(corpus)
    queries = [generate_query(f) for f in facts]
    bundles = [build_bundle(f, baseline) for f in facts]
    evidence = [r for b in bundles for r in b['evidence']]
    reverse = LexicalRetrievalBaseline(corpus[::-1])
    deterministic = bundles == [build_bundle(copy.deepcopy(f), reverse) for f in facts]
    provenance_complete = all(all(r['provenance'][k] for k in ('paper_id','chunk_id','title')) for r in evidence)
    write_jsonl(output/'sensor_inputs.jsonl', facts)
    write_jsonl(output/'evidence_queries.jsonl', queries)
    write_jsonl(output/'retrieval_results.jsonl', [{'evidence':b['evidence']} for b in bundles])
    write_jsonl(output/'evidence_bundles.jsonl', bundles)
    lines = ['# Phase 5F オフライン診断', '',
             '**すべて合成テスト文献。研究上の実証的根拠ではない。**', '',
             f'query数: {len(queries)} / コーパスchunk数: {len(corpus)} / top_k: 5',
             f'取得総数: {len(evidence)} / 0件query数: {sum(not b["evidence"] for b in bundles)}',
             f'一致語数合計: {sum(len(r["matched_terms"]) for r in evidence)}',
             f'決定的順序（逆順コーパスと比較）: {deterministic}',
             f'必須provenance完全性: {provenance_complete}', '',
             '検索性能の科学的評価ではなく、データ契約の工学的診断である。']
    for name, query, bundle in zip(names, queries, bundles):
        lines += ['', '## ' + name, '', '検索語: ' + json.dumps(query['keywords'], ensure_ascii=False),
                  '数値（検索語とは分離）: ' + json.dumps(query['observed_values'], ensure_ascii=False)]
        for r in bundle['evidence']:
            lines += [f'- 合成テスト文献 {r["paper_id"]}/{r["chunk_id"]}: '
                      f'score={r["retrieval_score"]}, matched_terms={r["matched_terms"]}']
        if not bundle['evidence']:
            lines += ['検索語なし・取得0件。状態を補完しない。']
    (output/'retrieval_summary_ja.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return bundles


def main():
    parser = argparse.ArgumentParser(description='Offline synthetic evidence engineering demo')
    parser.add_argument('--output', default='/tmp/ros4hc_phase5f')
    parser.add_argument('--config', required=True, help='Phase 5A canonical config')
    args = parser.parse_args()
    try:
        run_demo(args.output, args.config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print('合成テスト文献・研究上の実証的根拠ではない: ' + args.output)

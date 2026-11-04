"""Deterministic lexical engineering baseline, not semantic retrieval."""
import argparse
import copy
import hashlib
import json
import re
from .literature_corpus import load_corpus, validate_corpus
from .evidence import build_bundle, read_sensor_inputs, write_jsonl


def tokens(text):
    return set(re.findall(r'[a-z]+', text.casefold()))


class LexicalRetrievalBaseline:
    def __init__(self, corpus):
        self.corpus = validate_corpus(corpus)

    def retrieve(self, query, top_k=5):
        if type(top_k) is not int or top_k < 0:
            raise ValueError('top_k must be a nonnegative integer')
        terms = tokens(' '.join(query['keywords']))
        results = []
        for record in self.corpus:
            text = ' '.join(record[k] or '' for k in ('title', 'evidence_text'))
            matched = sorted(terms & tokens(text))
            modalities = sorted(set(query['modalities']) & set(record['sensor_modalities'] or []))
            variables = sorted(set(query['variables']) & set(record['measured_variables'] or []))
            score = len(matched) + 4 * len(modalities) + 2 * len(variables)
            if not score:
                continue
            provenance = {k: copy.deepcopy(record[k]) for k in
                          ('paper_id', 'chunk_id', 'title', 'authors', 'year', 'doi', 'url')}
            digest = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            results.append(dict(paper_id=record['paper_id'], chunk_id=record['chunk_id'],
                                title=record['title'], provenance=provenance,
                                corpus_record_sha256=digest, evidence_text=record['evidence_text'],
                                synthetic_fixture=record['synthetic_fixture'], retrieval_score=score,
                                matched_terms=matched,
                                sensor_relevance=dict(matched_modalities=modalities, matched_variables=variables),
                                limitations=['Lexical overlap only; no scientific relevance validation'] +
                                (['合成テスト文献・研究上の実証的根拠ではない'] if record['synthetic_fixture'] else [])))
        return sorted(results, key=lambda r: (-r['retrieval_score'], r['paper_id'], r['chunk_id']))[:top_k]


def main():
    parser = argparse.ArgumentParser(description='Retrieve local evidence and construct bundles offline')
    parser.add_argument('--sensor-input', required=True)
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--output', required=True, help='EvidenceBundle JSONL')
    parser.add_argument('--retrieval-output', help='Optional evidence-array JSONL')
    parser.add_argument('--top-k', type=int, default=5)
    args = parser.parse_args()
    try:
        retriever = LexicalRetrievalBaseline(load_corpus(args.corpus))
        bundles = [build_bundle(f, retriever, args.top_k) for f in read_sensor_inputs(args.sensor_input)]
        write_jsonl(args.output, bundles)
        if args.retrieval_output:
            write_jsonl(args.retrieval_output, [{'evidence': b['evidence']} for b in bundles])
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))

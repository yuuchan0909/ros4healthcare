"""Offline controlled audit of the existing verified registry; no paper acquisition."""
import argparse
from pathlib import Path
from .condition_aware_retrieval import ConditionAwareRetrievalBaseline
from .evidence import generate_query, write_jsonl
from .literature_curation import export_registry
from .literature_registry import load_registry


def direct_query(modalities, variables, keywords):
    return dict(modalities=modalities, variables=variables, keywords=keywords, population=None,
                clinical_condition=None, temporal_features=[], observed_values={})


def controlled_queries():
    return {
        'A_hr': generate_query({'hr': {'mean_hr_bpm': 82}}),
        'B_activity': generate_query({'acc': {'activity_magnitude_mean': 1}}),
        'C_hr_activity': generate_query({'hr': {'mean_hr_bpm': 82}, 'acc': {'activity_magnitude_mean': 1}}),
        'D_gait_speed': direct_query(['activity'], ['gait speed'], ['gait speed']),
        'E_tug': direct_query(['activity'], ['Timed Up and Go time'], ['TUG']),
        'F_environment': direct_query(['home images'], ['environmental fall hazards'], ['multimodal', 'home images']),
    }


def evaluate(corpus):
    retriever = ConditionAwareRetrievalBaseline(corpus)
    return [dict(case=name, query=query, evidence=retriever.retrieve(query, len(corpus)),
                 audit=retriever.audit(query)) for name, query in controlled_queries().items()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--papers', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    papers, chunks, _ = load_registry(args.papers, args.evidence)
    corpus = export_registry(papers, chunks, args.output)
    write_jsonl(Path(args.output) / 'controlled_retrieval.jsonl', evaluate(corpus))
    print(f'{len(papers)} papers / {len(corpus)} verified-only chunks')


if __name__ == '__main__':
    main()

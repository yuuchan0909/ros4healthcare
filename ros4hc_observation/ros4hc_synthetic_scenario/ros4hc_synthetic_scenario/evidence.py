"""Measurement queries and future-model bundles, without interpretation."""
import argparse
import copy
import json
import math
from datetime import datetime
from typing import TypedDict
from pathlib import Path
from .sensor_language import HR_FIELDS, ACC_FIELDS, audit_input


class EvidenceQuery(TypedDict):
    modalities: list
    variables: list
    temporal_features: list
    keywords: list
    population: object
    clinical_condition: object
    observed_values: dict


class EvidenceRecord(TypedDict):
    paper_id: str
    chunk_id: str
    title: str
    provenance: dict
    corpus_record_sha256: str
    evidence_text: str
    synthetic_fixture: object
    retrieval_score: int
    matched_terms: list
    sensor_relevance: dict
    limitations: list


class EvidenceBundle(TypedDict):
    query: EvidenceQuery
    sensor_facts: dict
    evidence: list[EvidenceRecord]
    retrieval_metadata: dict


def write_jsonl(path, records):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                           allow_nan=False) + '\n' for r in records), encoding='utf-8')


def read_sensor_inputs(path):
    records = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    # Phase 5B structured exports have evaluator envelope fields. Only the facts cross the boundary.
    return [r['task_input'] if set(r) == {'sample_id', 'condition', 'task_input'} else r for r in records]


def generate_query(facts) -> EvidenceQuery:
    if not isinstance(facts, dict):
        raise ValueError('Structured Phase 5B facts required')
    audit_input(facts)  # recursive allowlist, including nested metadata
    windows = facts.get('windows', [facts])
    if not isinstance(windows, list):
        raise ValueError('windows must be a list')
    if 'windows' in facts and windows:
        offsets = [w.get('relative_minutes') if isinstance(w, dict) else None for w in windows]
        if (any(type(v) not in (int, float) or not math.isfinite(v) or v > 0 for v in offsets)
                or offsets[-1] != 0 or any(a >= b for a, b in zip(offsets, offsets[1:]))):
            raise ValueError('History must be ordered past-to-current; future windows forbidden')
        if any('start_time' in w or 'end_time' in w for w in windows):
            try:
                starts = [datetime.fromisoformat(w['start_time']) for w in windows]
                ends = [datetime.fromisoformat(w['end_time']) for w in windows]
                if (any(t.utcoffset() is None for t in starts + ends) or
                        any(a >= b for a, b in zip(starts, ends)) or
                        any(a > b for a, b in zip(ends, starts[1:]))):
                    raise ValueError('Invalid history timestamps')
            except (KeyError, TypeError) as exc:
                raise ValueError('Incomplete history timestamps') from exc
    modalities, variables, trends, keywords, observed = set(), set(), set(), set(), {}
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            raise ValueError('window must be an object')
        values = {}
        for modality, fields in [('hr', HR_FIELDS), ('acc', ACC_FIELDS)]:
            source = window.get(modality, {})
            if not isinstance(source, dict):
                raise ValueError('modality must be an object')
            for key in fields:
                value = source.get(key)
                if key == 'trend':
                    if value not in (None, 'unavailable', 'increasing', 'decreasing', 'stable'):
                        raise ValueError('Unknown measurement trend')
                    if value in ('increasing', 'decreasing', 'stable'):
                        trends.add(value)
                        variables.add(key)
                        modalities.add('activity')
                        keywords.update(('physical activity', 'movement intensity', 'temporal change'))
                    continue
                if value is None:
                    continue
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError('Measurement must be finite numeric/null')
                values[key] = value
                variables.add(key)
                modalities.add('hr' if modality == 'hr' else 'activity')
                keywords.update(('heart rate',) if modality == 'hr' else ('physical activity', 'movement intensity'))
                if key == 'hr_change_bpm':
                    keywords.update(('heart rate change', 'temporal change'))
                    trends.add('increasing' if value > 0 else 'decreasing' if value < 0 else 'stable')
        if 'windows' in facts:
            observed[str(index)] = values
        else:
            observed = values
    if len(modalities) == 2:
        keywords.add('multimodal sensing')
    return dict(modalities=sorted(modalities), variables=sorted(variables),
                temporal_features=sorted(trends), keywords=sorted(keywords), population=None,
                clinical_condition=None, observed_values=observed)


def build_bundle(facts, retriever, top_k=5) -> EvidenceBundle:
    query = generate_query(facts)
    return {'query': query, 'sensor_facts': copy.deepcopy(facts),
            'evidence': retriever.retrieve(query, top_k),
            'retrieval_metadata': {'method': 'lexical_baseline', 'top_k': top_k,
                                   'corpus_record_count': len(retriever.corpus)}}


def main():
    parser = argparse.ArgumentParser(description='Generate measurement-only evidence queries offline')
    parser.add_argument('--sensor-input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        write_jsonl(args.output, [generate_query(f) for f in read_sensor_inputs(args.sensor_input)])
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))

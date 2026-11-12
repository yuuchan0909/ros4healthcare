import csv
import json
from pathlib import Path
import pytest

from ros4hc_synthetic_scenario import knowledge_extraction_evaluation as e


class FakeAdapter:
    def __init__(self):
        self.records = []
        counts = {
            'experimental_method': 10,
            'measurement_method': 6,
            'analysis_method': 7,
            'empirical_finding': 7,
            'limitation': 6,
            'study_design': 4,
            'review_statement': 3,
            'other': 3,
        }
        self.paragraphs = []
        self.lookup = {}
        pnum = 0
        for category, count in {'methods': 12, 'results': 10, 'discussion_limitations': 8, 'other': 6}.items():
            for _ in range(count):
                pnum += 1
                p = dict(paragraph_id=f'paragraph_{pnum:04d}', section_id=f'section_{pnum:04d}',
                    section_title={'methods':'Methods','results':'Results','discussion_limitations':'Discussion','other':'Background'}[category], role='body', kind='p', source_order=pnum,
                    source_text=('Substantive source text ' + str(pnum) + ' ') * 8,
                    source_text_sha256=f'hash-{pnum}')
                self.paragraphs.append(p); self.lookup[p['paragraph_id']] = p
        rid = 0
        paragraphs = list(self.lookup.values())
        for typ, count in counts.items():
            for _ in range(count):
                rid += 1
                p = paragraphs[(rid - 1) % len(paragraphs)]
                self.records.append(dict(knowledge_id=f'knowledge_{rid:04d}', knowledge_type=typ,
                    target_concepts=['target'], sensors=[], measurements=[], experimental_methods=['method'] if typ=='experimental_method' else [],
                    analysis_methods=['analysis'] if typ=='analysis_method' else [], conditions=[], outcomes=[], findings=['finding'] if typ=='empirical_finding' else [],
                    limitations=['limit'] if typ=='limitation' else [], applicability_notes=[],
                    population={'description': None, 'sample_size': None, 'age': None, 'setting': None},
                    provenance=[{k:p[k] for k in ('paragraph_id','section_id','section_title','source_text_sha256')}]))
    def audit(self):
        return {'unchanged': True, 'records': len(self.records)}


def test_fixed_samples_are_deterministic():
    a = FakeAdapter()
    k1 = e._knowledge_rows(a, 30, 'seed')
    k2 = e._knowledge_rows(a, 30, 'seed')
    assert [r['knowledge_id'] for r in k1] == [r['knowledge_id'] for r in k2]
    assert len(k1) == 30 and len({r['knowledge_id'] for r in k1}) == 30
    counts = {}
    for r in k1: counts[r['knowledge_type']] = counts.get(r['knowledge_type'], 0) + 1
    assert counts == e.KNOWLEDGE_QUOTAS


def test_coverage_is_blind_and_stratified():
    a = FakeAdapter()
    assert all('category' not in p for p in a.paragraphs)  # Matches real Phase 5M source_segments.jsonl.
    rows = e._coverage_rows(a, 20, 'seed')
    assert len(rows) == 20
    assert all('knowledge_id' not in r and 'findings' not in r for r in rows)
    assert all(r['gold_extractable'] == '' and r['gold_knowledge_items'] == '' for r in rows)
    counts = {}
    for r in rows: counts[r['category']] = counts.get(r['category'], 0) + 1
    assert counts == e.COVERAGE_QUOTAS


def test_prepare_and_validate(tmp_path, monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(e, 'MachineKnowledgeAdapter', lambda source: fake)
    out = tmp_path/'out'
    m = e.prepare(tmp_path/'source', out, 'seed')
    assert m['knowledge_sample_size'] == 30 and m['coverage_sample_size'] == 20
    assert (out/'coverage_blind.csv').exists() and (out/'knowledge_review.csv').exists()
    assert (out/'sampling_manifest.json').exists() and (out/'EVALUATION_GUIDE.md').exists()
    result = e.validate_reviews(out)
    assert result == {'knowledge_completed': 0, 'knowledge_total': 30, 'coverage_completed': 0, 'coverage_total': 20}


def test_completed_rows_require_labels(tmp_path, monkeypatch):
    fake = FakeAdapter(); monkeypatch.setattr(e, 'MachineKnowledgeAdapter', lambda source: fake)
    out = tmp_path/'out'; e.prepare(tmp_path/'source', out, 'seed')
    path = out/'knowledge_review.csv'
    with path.open(newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f)); fields = rows[0].keys()
    rows[0]['review_status'] = 'completed'
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    with pytest.raises(ValueError, match='completed row has blank Knowledge labels'):
        e.validate_reviews(out)


def test_refuses_overwrite(tmp_path, monkeypatch):
    fake = FakeAdapter(); monkeypatch.setattr(e, 'MachineKnowledgeAdapter', lambda source: fake)
    out = tmp_path/'out'; e.prepare(tmp_path/'source', out, 'seed')
    with pytest.raises(ValueError, match='Refuse overwrite'):
        e.prepare(tmp_path/'source', out, 'seed')

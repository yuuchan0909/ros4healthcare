"""Synthetic unit fixtures and integration with the existing three real papers."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.condition_aware_retrieval import ConditionAwareRetrievalBaseline, applicability
from ros4hc_synthetic_scenario.condition_aware_evaluation import controlled_queries, direct_query, evaluate
from ros4hc_synthetic_scenario.literature_curation import convert_registry, fall_risk_demo_records
from ros4hc_synthetic_scenario.literature_registry import load_registry
from ros4hc_synthetic_scenario.literature_retrieval import LexicalRetrievalBaseline
from ros4hc_synthetic_scenario.literature_corpus import load_corpus

ROOT = Path(__file__).parents[1]


@pytest.fixture
def real():
    papers, chunks, report = load_registry(ROOT/'literature/real/papers.jsonl', ROOT/'literature/real/evidence_chunks.jsonl')
    assert report['paper_count'] == 3 and report['evidence_chunk_count'] == 6
    corpus = convert_registry(papers, chunks)
    assert len(corpus) == 6 and all(not r['synthetic_fixture'] for r in corpus)
    return corpus


@pytest.fixture
def synthetic():
    papers, chunks = fall_risk_demo_records()
    return convert_registry(papers, chunks, mode='synthetic')[0]


def test_real_controlled(real):
    rows = {r['case']: r for r in evaluate(real)}
    assert rows['A_hr']['evidence'] == []
    assert all(not r['applicability']['measurement']['supported_query_modalities'] for r in rows['A_hr']['audit'])
    for case in ('B_activity', 'C_hr_activity'):
        assert len(rows[case]['evidence']) == 4
        assert {r['paper_id'] for r in rows[case]['evidence']} == {'paper_0001', 'paper_0002'}
        assert all(r['applicability']['overall_status'] == 'partial' for r in rows[case]['evidence'])
        assert all(r['applicability']['measurement']['measurement_status'] == 'mismatch'
                   for r in rows[case]['audit'] if r['paper_id'] == 'paper_0003')
    for case, chunk in (('D_gait_speed', 'chunk_0001'), ('E_tug', 'chunk_0002')):
        first = rows[case]['evidence'][0]
        assert (first['paper_id'], first['chunk_id']) == ('paper_0001', chunk)
        assert first['applicability']['overall_status'] == 'compatible'
        assert first['applicability']['fall_outcome']['record_conditions']['prospective_or_retrospective'] == 'retrospective'
    assert {r['paper_id'] for r in rows['F_environment']['evidence']} == {'paper_0003'}
    assert rows['F_environment']['evidence'][0]['chunk_id'] == 'chunk_0001'
    for case in rows.values():
        for row in case['audit']:
            assert row['applicability']['population']['population_status'] == 'unknown'
            outcome = row['applicability']['fall_outcome']['fall_outcome_status']
            assert outcome == {'paper_0001': 'documented', 'paper_0002': 'unknown', 'paper_0003': 'no_fall_outcome'}[row['paper_id']]


def test_determinism_no_mutation(real):
    original = copy.deepcopy(real)
    queries = controlled_queries()
    before = copy.deepcopy(queries)
    for q in queries.values():
        retriever = ConditionAwareRetrievalBaseline(real)
        assert retriever.retrieve(q) == retriever.retrieve(q)
        assert retriever.audit(q) == ConditionAwareRetrievalBaseline(real[::-1]).audit(q)
        assert retriever.retrieve(q) == ConditionAwareRetrievalBaseline(real[::-1]).retrieve(q)
    assert queries == before and real == original


def test_missing_unknown_no_fallback(synthetic):
    r = copy.deepcopy(synthetic)
    del r['curation_provenance']['fall_risk']
    q = direct_query(['hr'], ['mean_hr_bpm'], ['heart rate'])
    a = applicability(q, r)
    assert a['measurement']['measurement_status'] == 'unknown'
    assert a['population']['population_status'] == 'unknown'
    assert a['fall_outcome']['fall_outcome_status'] == 'unknown'
    assert ConditionAwareRetrievalBaseline([r]).retrieve(q) == []
    q['population'] = {'age': 85}
    q['clinical_condition'] = 'frailty'
    assert applicability(q, r)['population']['population_status'] == 'unknown'


def test_synthetic_structured_candidate_and_partial(synthetic):
    r = copy.deepcopy(synthetic)
    r['title'] = 'Synthetic fixture'
    r['evidence_text'] = 'Synthetic fixture'
    r['sensor_modalities'] = []
    r['measured_variables'] = []
    r['curation_provenance']['fall_risk']['measurement'].update(sensor_modalities=['accelerometers'], measured_variables=['gait speed'])
    q = direct_query(['activity'], ['activity_magnitude_mean'], [])
    result = ConditionAwareRetrievalBaseline([r]).retrieve(q)[0]
    assert result['lexical_signal'] == 0
    assert result['applicability']['overall_status'] == 'partial'
    q['modalities'] = ['hr', 'activity']
    m = applicability(q, r)['measurement']
    assert m['supported_query_modalities'] == ['activity'] and m['unsupported_query_modalities'] == ['hr']
    assert m['unmatched_variables'] == ['activity_magnitude_mean']
    q = direct_query(['imu'], ['gait speed'], [])
    assert applicability(q, r)['measurement']['measurement_status'] == 'match'
    q['modalities'] = ['hr']
    assert applicability(q, r)['measurement']['measurement_status'] == 'mismatch'
    q['modalities'] = []
    assert applicability(q, r)['measurement']['measurement_status'] == 'unknown'


def test_numerical_results_do_not_rank(real):
    changed = copy.deepcopy(real)
    for r in changed:
        f = r['curation_provenance']['fall_risk']
        f['risk_indicator']['threshold_value'] = 999
        f['results']['auc'] = 0.01
        f['results']['effect_size'] = {'measure': 'odds ratio', 'value': 999, 'unit': None}
    for q in controlled_queries().values():
        assert ConditionAwareRetrievalBaseline(real).audit(q) == ConditionAwareRetrievalBaseline(changed).audit(q)


def test_provenance_bundle_and_limits(real):
    retriever = ConditionAwareRetrievalBaseline(real)
    bundle = retriever.build_bundle({'acc': {'activity_magnitude_mean': 1}})
    assert bundle['retrieval_metadata']['method'] == retriever.method
    for result in bundle['evidence']:
        record = next(r for r in retriever.corpus if (r['paper_id'], r['chunk_id']) == (result['paper_id'], result['chunk_id']))
        assert result['provenance']['curation']['fall_risk'] == record['curation_provenance']['fall_risk']
        assert result['provenance']['doi'] == record['doi']
        assert result['corpus_record_sha256'] == hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert retriever.retrieve(controlled_queries()['B_activity'], 0) == []
    for k in (-1, True, 1.5):
        with pytest.raises(ValueError):
            retriever.retrieve(controlled_queries()['B_activity'], k)


def test_lexical_frozen_reference():
    corpus = load_corpus(ROOT/'ros4hc_synthetic_scenario/fixtures/synthetic_literature_corpus.jsonl')
    result = LexicalRetrievalBaseline(corpus).retrieve(controlled_queries()['C_hr_activity'], 10)
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert digest == '968f962361b5e4296f2a2418ca1655fb18d3db0dffe8d2d58de9f9ae91a31da2'

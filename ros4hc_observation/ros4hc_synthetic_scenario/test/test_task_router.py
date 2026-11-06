"""Language boundary and deterministic knowledge routing tests, without API calls."""
import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario import task_router as m
from ros4hc_synthetic_scenario import connector_registry as c
from ros4hc_synthetic_scenario.llm_providers import LLMResponse, PermanentProviderError


@pytest.fixture
def facts():
    return {'available_history_windows': 6, 'windows': [
        {'relative_minutes': t, 'hr': {'mean_hr_bpm': 65}, 'acc': {'activity_magnitude_mean': 0.1}}
        for t in (-30, -25, -20, -15, -10, -5, 0)]}


@pytest.mark.parametrize('case', ['A', 'B', 'C', 'D'])
def test_schema_deterministic_and_no_mutation(case):
    value = m.fixture_task(case); before = copy.deepcopy(value)
    assert m.parse_task(json.dumps(value)) == m.validate_task(value) == before
    assert c.route(value) == c.route(copy.deepcopy(value))
    assert value == before
    assert m.TASK_SCHEMA['additionalProperties'] is False
    assert set(m.TASK_SCHEMA['required']) == set(m.TASK_SCHEMA['properties'])


@pytest.mark.parametrize('mutation', ['extra', 'connector', 'flag', 'task', 'boolean', 'duplicate'])
def test_injection_and_malformed_spec_rejected(mutation):
    value = m.fixture_task('D')
    if mutation == 'extra': value['connector'] = 'WebSearchConnector'
    if mutation == 'connector': value['required_information'] = ['shell_exec']
    if mutation == 'flag': value['requires_scholarly_evidence'] = False
    if mutation == 'task': value['task_type'] = 'autonomous_diagnosis'
    if mutation == 'boolean': value['requires_scholarly_evidence'] = 1
    if mutation == 'duplicate': value['required_information'] *= 2
    with pytest.raises(ValueError): c.route(value)


def test_duplicate_json_keys_rejected():
    with pytest.raises(ValueError): m.parse_task('{"task_type":"unknown","task_type":"unknown"}')


def test_unknown_handling():
    task = m.fixture_task('A')
    task.update(task_type='unknown', required_information=[], ambiguities=['Clarify target'])
    for k in m.REQUIRES: task[k] = False
    context = c.build_context('???', task)
    assert context['connector_results'] == [] and context['coverage_gaps'] == ['task_clarification']


def test_controlled_routes_and_missing_sources(facts):
    results = {case: c.build_context(text, m.fixture_task(case), facts) for case, text in m.EXAMPLES}
    assert results['A']['connector_results'][0]['connector'] == 'SensorHistoryConnector'
    assert results['A']['connector_results'][0]['status'] == 'available'
    b = results['B']; assert set(b['coverage_gaps']) == {'scholarly_evidence', 'measurement_methods', 'sensor_capabilities'}
    assert all(r['status'] == 'unavailable' and r['data'] is None for r in b['connector_results'])
    assert set(results['C']['coverage_gaps']) == {'experimental_methods', 'analysis_methods', 'sensor_capabilities'}
    d = results['D']['connector_results'][0]
    assert d['status'] == 'available' and d['data']['retrieval_mode'] == 'condition_aware_final_evidence'
    assert len(d['data']['evidence']) == 4 and d['data']['measurement_coverage_gaps'] == ['hr']
    assert all(e['applicability']['overall_status'] in ('partial', 'compatible') for e in d['data']['evidence'])
    assert 'audit' not in d['data']


def test_topic_only_lookup_does_not_invent_measurements():
    context = c.build_context(m.EXAMPLES[3][1], m.fixture_task('D'))
    r = context['connector_results'][0]
    assert r['status'] == 'available' and r['data']['retrieval_mode'] == 'topic_lookup_only'
    assert r['knowledge_request']['measurements'] == []
    assert all(e['applicability']['overall_status'] == 'unknown' for e in r['data']['evidence'])
    assert context['coverage_gaps'] == ['evidence_applicability_unassessed']


@pytest.mark.parametrize('text,targets', [
    ('パーキンソン病の研究に必要なセンサーは？', ["Parkinson's disease"]),
    ('パーキンソン病の研究に必要なセンサーは？', ['falls']),
    ('転倒とパーキンソン病に関連する研究を調べたい', ['falls']),
    ('転倒に関連する研究を調べたい', ['falls', "Parkinson's disease"]),
    ('find research on diabetes and falls', ['falls']),
    ('find research on diabetes', ['diabetes']),
])
def test_domain_mismatch_blocks_before_corpus_lookup(text, targets, tmp_path):
    task = m.fixture_task('D'); task['target_concepts'] = targets
    context = c.build_context(text, task, corpus_dir=tmp_path/'does_not_exist')
    r = context['connector_results'][0]
    assert r['status'] == 'unavailable' and r['data'] is None
    assert r['target_mismatch_detected'] is True
    assert context['coverage_gaps'] == ['scholarly_evidence']


def test_all_information_maps_to_allowlist_and_stubs():
    task = m.fixture_task('C'); task['required_information'] = list(m.INFORMATION)
    for k in m.REQUIRES: task[k] = True
    assert [r['connector'] for r in c.route(task)] == list(c.CONNECTOR_NAMES)
    result = c.build_context('研究計画', task)
    action = next(r for r in result['connector_results'] if r['connector'] == 'ActionPolicyConnector')
    assert action['status'] == 'unavailable' and 'not_implemented' in action['reason']


@pytest.mark.parametrize('field', ['sample_id', 'evaluation_ground_truth', 'hidden_activity_label'])
def test_metadata_rejected(facts, field):
    facts['windows'][0][field] = 'SECRET_VALUE'
    with pytest.raises(ValueError): c.build_context(m.EXAMPLES[0][1], m.fixture_task('A'), facts)


def test_missing_history_and_input_mutation(facts):
    task = m.fixture_task('A'); before = copy.deepcopy((task, facts))
    context = c.build_context(m.EXAMPLES[0][1], task, facts)
    assert (task, facts) == before
    context['connector_results'][0]['data']['windows'][0]['hr']['mean_hr_bpm'] = 999
    assert (task, facts) == before
    assert c.build_context(m.EXAMPLES[0][1], task)['unavailable_sources'] == ['SensorHistoryConnector']


def test_router_payload_no_observation_metadata_or_tools():
    req = m.make_request(m.EXAMPLES[0][1])
    payload = m.OpenAIProvider._payload(m.MODEL, req, m.DECODING)
    assert payload['store'] is False and payload['reasoning'] == {'effort': 'medium'}
    assert not {'tools', 'web', 'temperature', 'max_output_tokens'} & payload.keys()
    assert all(k not in req['prompt'] for k in ('sample_id', 'evaluation_ground_truth', 'hidden_activity_label'))
    assert set(json.loads(req['prompt'].split('INPUT_DATA_JSON:\n')[1])) == {'user_request'}


class Fake:
    def __init__(self, fail=False): self.calls=0; self.fail=fail
    def generate(self, request, decoding):
        self.calls += 1
        if self.fail and self.calls == 1: raise PermanentProviderError()
        text = json.loads(request['prompt'].split('INPUT_DATA_JSON:\n')[1])['user_request']
        case = next(case for case, t in m.EXAMPLES if t == text)
        return LLMResponse(json.dumps(m.fixture_task(case)), {'total_tokens': 10})


def test_dry_run_zero_calls_key_not_saved(tmp_path, monkeypatch, facts):
    monkeypatch.setenv('OPENAI_API_KEY', 'TEST_KEY_SHOULD_NEVER_PERSIST')
    provider = Fake()
    result = m.run(tmp_path/'dry', True, provider, facts)
    assert provider.calls == result['actual_api_calls'] == 0
    assert result['status'] == 'success'
    for p in (tmp_path/'dry').iterdir():
        assert 'TEST_KEY_SHOULD_NEVER_PERSIST' not in p.read_text()
    with pytest.raises(ValueError): m.base.save(tmp_path/'bad', {'key':'TEST_KEY_SHOULD_NEVER_PERSIST'})


def test_four_calls_and_failure_checkpoint(tmp_path, facts):
    provider=Fake(True)
    manifest=m.run(tmp_path/'mock', provider=provider, sensor_facts=facts)
    assert manifest['actual_api_calls'] == provider.calls == 4
    assert manifest['status'] == 'completed_with_failures'
    rows=json.loads((tmp_path/'mock/results.json').read_text())
    assert rows[0]['status'] == 'invalid_or_failed' and all(r['status']=='success' for r in rows[1:])
    assert manifest['token_usage']['total_tokens']==30


def test_retrieved_corpus_target_mismatch_rejected(monkeypatch):
    monkeypatch.setattr(c.base, 'convert_registry', lambda *a: [dict(
        paper_id='paper_0001', doi='10.1186/s12877-026-07329-6', title='Parkinson disease sensors')])
    context = c.build_context(m.EXAMPLES[3][1], m.fixture_task('D'))
    result = context['connector_results'][0]
    assert result['status'] == 'unavailable' and result['data'] is None
    assert result['target_mismatch_detected'] is True


def test_absent_knowledge_unavailable(tmp_path):
    result = c.build_context(m.EXAMPLES[3][1], m.fixture_task('D'), corpus_dir=tmp_path)
    assert result['connector_results'][0]['status'] == 'unavailable'
    assert result['coverage_gaps'] == ['scholarly_evidence']
    result = c.build_context(m.EXAMPLES[0][1], m.fixture_task('A'), sensor_facts={})
    assert result['connector_results'][0]['status'] == 'unavailable'

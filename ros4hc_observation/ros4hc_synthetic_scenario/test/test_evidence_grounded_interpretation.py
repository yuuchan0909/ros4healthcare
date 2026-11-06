"""Phase 5I boundaries, real-corpus integration and transport-free execution."""
import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario import evidence_grounded_interpretation as m
from ros4hc_synthetic_scenario.llm_providers import LLMResponse, TransientProviderError, PermanentProviderError

ROOT = Path(__file__).parents[1]


@pytest.fixture
def context():
    facts = {'hr': {'mean_hr_bpm': 82}, 'acc': {'activity_magnitude_mean': 1}}
    papers, chunks, _ = m.load_registry(ROOT/'literature/real/papers.jsonl', ROOT/'literature/real/evidence_chunks.jsonl')
    corpus = m.convert_registry(papers, chunks)
    bundle = m.ConditionAwareRetrievalBaseline(corpus).build_bundle(facts)
    return facts, bundle


def answer(data):
    r = data['evidence'][0]
    return dict(answer_ja='現時点では判断できない', observed_facts=['心拍数82 bpm'],
        evidence_supported_points=[dict(statement_ja='研究内容', paper_id=r['paper_id'], chunk_id=r['chunk_id'], caveat_ja='一部対応')],
        unsupported_or_unknown=['対象集団未確認'], additional_information_needed=['測定条件'])


class Fake:
    def __init__(self, outcomes):
        self.outcomes, self.calls = outcomes, 0

    def generate(self, request, decoding):
        value = self.outcomes[min(self.calls, len(self.outcomes)-1)]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return LLMResponse(value, {'total_tokens': 10})


def test_final_only_determinism_and_no_mutation(context):
    facts, bundle = context
    before = copy.deepcopy((facts, bundle))
    data, req = m.prepare(facts, bundle)
    assert (facts, bundle) == before
    assert (data, req) == m.prepare(facts, bundle)
    assert data['coverage_gaps'] == ['hr']
    assert len(data['evidence']) == 4
    assert {r['paper_id'] for r in data['evidence']} == {'paper_0001', 'paper_0002'}
    assert 'retrieval_metadata' not in req['prompt'] and 'audit' not in data
    assert all(r['applicability']['overall_status'] == 'partial' for r in data['evidence'])
    for forbidden in ('sample_id', 'evaluation_ground_truth', 'scenario_label', 'expected_state', 'predicted_state', 'hidden_activity_label'):
        assert forbidden not in req['prompt']
    payload = m.OpenAIProvider._payload(m.MODEL, req, m.DECODING)
    assert payload['model'] == 'gpt-6-astra' and payload['reasoning']['effort'] == 'medium'
    assert payload['store'] is False
    assert not {'temperature', 'max_output_tokens', 'tools'} & payload.keys()
    assert m.prepare(facts, dict(bundle, evidence=[]))[0]['coverage_gaps'] == ['activity', 'hr']


@pytest.mark.parametrize('key', ['sample_id', 'evaluation_ground_truth', 'expected_state', 'hidden_activity_label'])
def test_leak_rejected(context, key):
    facts, bundle = copy.deepcopy(context)
    facts[key] = 'SECRET_LABEL'
    with pytest.raises(ValueError):
        m.prepare(facts, bundle)


def test_selection_file_order_and_envelope(tmp_path):
    rows = [dict(sample_id='do-not-send', evaluation_ground_truth='do-not-send', task_input={
        'available_history_windows': n, 'hr': {'mean_hr_bpm': value}}) for n, value in [(5, 1), (6, 82), (6, 99)]]
    path = tmp_path/'input.jsonl'
    path.write_text('\n'.join(map(json.dumps, rows)))
    assert m.select_sensor(path) == rows[1]['task_input']


def test_mismatch_not_accepted(context):
    facts, bundle = copy.deepcopy(context)
    bundle['evidence'][0]['applicability']['overall_status'] = 'mismatch'
    with pytest.raises(ValueError):
        m.prepare(facts, bundle)


@pytest.mark.parametrize('identity', [('nonexistent', 'nonexistent'), ('paper_0003', 'chunk_0001')])
def test_invalid_citations(context, identity):
    data, _ = m.prepare(*context)
    value = answer(data)
    value['evidence_supported_points'][0].update(paper_id=identity[0], chunk_id=identity[1])
    with pytest.raises(ValueError):
        m.validate_response(json.dumps(value), data)


def test_duplicate_citation_and_renderer(context):
    data, _ = m.prepare(*context)
    value = answer(data)
    value['evidence_supported_points'] *= 2
    parsed = m.validate_response(json.dumps(value), data)
    report = m.render_ja(data, parsed)
    assert report == m.render_ja(data, parsed)
    assert 'DOI：' in report and '一部対応' in report and '心拍数に直接対応する文献' in report


@pytest.mark.parametrize('raw', ['not json', '{}', '[]', '{"answer_ja":"a","answer_ja":"b"}'])
def test_invalid_json_no_regeneration(context, raw):
    data, req = m.prepare(*context)
    provider = Fake([raw])
    with pytest.raises(ValueError):
        m.interpret(provider, req, data)
    assert provider.calls == 1


@pytest.mark.parametrize('error,calls', [(TransientProviderError(), 2), (PermanentProviderError(), 1)])
def test_retry_limits(context, error, calls):
    data, req = m.prepare(*context)
    provider = Fake([error])
    with pytest.raises(type(error)):
        m.interpret(provider, req, data)
    assert provider.calls == calls


def test_transient_then_success(context):
    data, req = m.prepare(*context)
    provider = Fake([TransientProviderError(), json.dumps(answer(data))])
    assert m.interpret(provider, req, data)[1] == {'total_tokens': 10}
    assert provider.calls == 2


def test_dry_run_no_provider_and_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    path = tmp_path/'sensor.jsonl'
    path.write_text(json.dumps(dict(sample_id='SECRET_ID', task_input={'available_history_windows': 6, 'hr': {'mean_hr_bpm': 82}, 'acc': {'activity_magnitude_mean': 1}})))
    provider = Fake([AssertionError('must not call')])
    result = m.run(path, ROOT/'literature/real', tmp_path/'out', True, provider)
    assert provider.calls == 0 and result['coverage_gaps'] == ['hr']
    assert len(list((tmp_path/'out').iterdir())) == 5
    assert 'SECRET_ID' not in (tmp_path/'out/model_input.json').read_text()


def test_secret_not_saved(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret-never-persist')
    with pytest.raises(ValueError):
        m.save(tmp_path/'secret.json', {'x': 'test-secret-never-persist'})
    assert not (tmp_path/'secret.json').exists()
    m.save(tmp_path/'safe.json', {'ok': True})
    assert 'test-secret-never-persist' not in (tmp_path/'safe.json').read_text()


def test_prompt_preserves_observation_and_trend_meaning(context):
    _, request = m.prepare(*context)
    instructions = request['prompt'].split('\nINPUT_DATA_JSON:\n', 1)[0]
    for constraint in (
        'Sensor Factsのfieldは提供された観測情報としてのみ扱い、ground-truth labelと呼ばない。',
        '入力に明示されていない生成方法を推測しない。',
        'trendは提供された時間変化情報としてのみ扱う。',
        'activity trendがstableでもfall riskがstableとは解釈しない。',
        '入力にsyntheticと明示されていても、trend自体を「付与されたラベル」と表現しない。',
    ):
        assert constraint in instructions

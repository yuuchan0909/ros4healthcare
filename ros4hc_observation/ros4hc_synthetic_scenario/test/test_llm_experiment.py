"""外部APIなしのPhase 5C配線・漏洩・再開・採点試験。"""
import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.dataset import LABELS, generate, load_config
from ros4hc_synthetic_scenario.sensor_language import export_inputs
from ros4hc_synthetic_scenario.llm_providers import (
    LLMProvider, MockProvider, LLMResponse, ExternalProvider, TransientProviderError, PermanentProviderError)
from ros4hc_synthetic_scenario.llm_experiment import (
    build_request, digest, import_responses, load_inputs, output_lock, read_journal,
    request_key, run_experiment, select_samples)


@pytest.fixture(scope='module')
def inputs(tmp_path_factory):
    path = tmp_path_factory.mktemp('phase5c_inputs')
    day = generate(load_config(Path(__file__).parents[1]/'configs/daily_subject_001.json'))
    export_inputs(day, path)
    return path


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def run(inputs, output, provider=None, **kwargs):
    return run_experiment(inputs, output, provider or MockProvider(),
                          conditions=('a',), representations=('structured',), retry_delay=0, **kwargs)


class ScriptedProvider(MockProvider):
    def __init__(self, script):
        super().__init__()
        self.script = iter(script)
        self.seen = []

    def generate(self, request, decoding):
        self.calls += 1
        self.seen.append(copy.deepcopy(request))
        next_value = next(self.script, LLMResponse('{"predicted_state":"indeterminate"}'))
        if isinstance(next_value, Exception):
            raise next_value
        return next_value


def test_provider_abstraction_and_mock():
    with pytest.raises(TypeError):
        LLMProvider()
    provider = MockProvider('specified-model')
    assert provider.model_identifier() == 'specified-model'
    assert provider.provider_identifier() == 'mock'
    response = provider.generate(build_request({'hr': {}, 'acc': {}}), {'temperature': 0})
    assert isinstance(response, LLMResponse) and response.raw_response
    assert provider.calls == 1


def test_request_contract_and_no_ground_truth():
    facts = {'hr': {'mean_hr_bpm': 138.5}, 'acc': {'trend': 'increasing'}}
    before = copy.deepcopy(facts)
    request = build_request(facts)
    assert set(request) == {'prompt', 'output_schema'}
    prefix, content = request['prompt'].split('観測事実:\n')
    assert json.loads(content) == facts
    assert all(state in prefix and ja in prefix for state, ja in LABELS.items())
    assert set(request['output_schema']['properties']['predicted_state']['enum']) == set(LABELS)
    assert 'ground_truth' not in json.dumps(request)
    assert facts == before


@pytest.mark.parametrize('facts', [
    {'ground_truth_state': 'high_activity'}, {'window_id': 'opaque'},
    {'acc': {'activity_level': 'high'}}, '心拍数138。missing_hr', 'subject_001_204',
    'window_id: opaque', 'original_ground_truth_state: high_activity', '回復中である。',
])
def test_unsafe_request_blocked(facts):
    with pytest.raises(ValueError):
        build_request(facts)


def test_pilot_paired_deterministic_and_rare_classes(inputs):
    evaluation = json.loads((inputs/'evaluation_ground_truth.json').read_text())
    selected, expected, _, tasks = load_inputs(inputs, tuple('abcd'), ('structured', 'ja'), 'clean', 'pilot', 42)
    assert len(selected) == 52 and len(tasks) == 416
    assert select_samples(evaluation) == selected
    assert select_samples(list(reversed(evaluation))) == selected
    assert select_samples(evaluation, seed=43) != selected
    counts = {state: list(expected.values()).count(state) for state in LABELS}
    assert list(counts.values()) == [16, 16, 8, 6, 6, 0]
    for condition in 'abcd':
        for representation in ('structured', 'ja'):
            assert [t['sample_id'] for t in tasks if t['condition'] == condition
                    and t['representation'] == representation] == selected
    for state in ('high_activity', 'post_activity_recovery'):
        assert {r['sample_id'] for r in evaluation if r['ground_truth_state'] == state} <= set(selected)


def test_full_mode_288_each_setting(inputs):
    selected, _, _, tasks = load_inputs(inputs, tuple('abcd'), ('structured', 'ja'), 'clean', 'full', 42)
    assert len(selected) == 288 and len(tasks) == 2304


def test_clean_excludes_perturbations(inputs):
    evaluation = json.loads((inputs/'evaluation_ground_truth.json').read_text())
    mixed = evaluation + [{**evaluation[0], 'sample_id': 'extra', 'variant': 'missing_hr',
                           'perturbations': [{'kind': 'missing_hr'}]}]
    assert select_samples(mixed) == select_samples(evaluation)
    assert select_samples(mixed, dataset='perturbations') == ['extra']


def test_cache_key_stability_and_sensitivity():
    request = build_request({'hr': {}, 'acc': {}})
    args = ['mock', 'm1', 'a', 'structured', 'sample1', request, {'temperature': 0}]
    assert request_key(*args) == request_key(*copy.deepcopy(args))
    baseline = request_key(*args)
    for index, value in [(0, 'other'), (1, 'm2'), (2, 'b'), (3, 'ja'), (4, 'sample2'),
                         (6, {'temperature': 1})]:
        modified = copy.deepcopy(args)
        modified[index] = value
        assert request_key(*modified) != baseline


def test_dry_run_all_settings_manifest(inputs, tmp_path):
    provider = MockProvider()
    report = run_experiment(inputs, tmp_path, provider, dry_run=True)
    assert provider.calls == 0
    assert len(read(tmp_path/'requests.jsonl')) == 416
    assert not read(tmp_path/'responses.jsonl')
    assert len(report['settings']) == 8
    assert all(s['total_count'] == 52 and s['completed_count'] == 0 for s in report['settings'].values())
    manifest = json.loads((tmp_path/'experiment_manifest.json').read_text())
    for field in ('experiment_id', 'timestamp', 'git_commit', 'provider', 'model', 'conditions',
                  'representations', 'task', 'decoding', 'number_of_samples', 'prompt_template_version',
                  'dataset_source_identifiers', 'mode', 'selected_sample_ids', 'implementation_sha256'):
        assert field in manifest
    assert manifest['number_of_samples'] == 52 and manifest['number_of_requests'] == 416
    assert manifest['decoding'] == {'temperature': 0.0}
    assert report['cost'] is None and report['usage'] is None
    assert '記述的差分' in (tmp_path/'scores_ja.md').read_text()
    assert 'モデル性能ではありません' in (tmp_path/'scores_ja.md').read_text()
    assert set(report['paired_descriptive_differences']) == {
        f'{pair}-{rep}' for pair in ('B-A', 'C-B', 'D-C') for rep in ('structured', 'ja')}


def test_resume_force_raw_and_scoring(inputs, tmp_path):
    provider = ScriptedProvider([LLMResponse('{"predicted_state":"low_activity"}',
                                            {'input_tokens': 12, 'output_tokens': 3})])
    first = run(inputs, tmp_path, provider)
    assert provider.calls == 52
    record = read(tmp_path/'responses.jsonl')[0]
    assert record['raw_response'] == '{"predicted_state":"low_activity"}'
    assert record['valid'] and record['parsed_result'] == {'predicted_state': 'low_activity'}
    assert first['usage'] == {'input_tokens': 12, 'output_tokens': 3}
    assert first['settings']['A-structured']['correct_count'] in (0, 1)
    run(inputs, tmp_path, provider)
    assert provider.calls == 52
    run(inputs, tmp_path, provider, force=True)
    assert provider.calls == 104
    assert len(read(tmp_path/'responses.jsonl')) == 104
    assert len(read(tmp_path/'requests.jsonl')) == 52


def test_invalid_json_not_retried_and_cached(inputs, tmp_path):
    provider = ScriptedProvider([LLMResponse('```json\n{"predicted_state":"high_activity"}\n```')])
    report = run(inputs, tmp_path, provider)
    assert provider.calls == 52
    first = read(tmp_path/'responses.jsonl')[0]
    assert first['raw_response'].startswith('```') and not first['valid']
    assert first['parsed_result'] is None and first['attempt_count'] == 1
    assert report['settings']['A-structured']['invalid_output_count'] == 1
    assert read(tmp_path/'failures.jsonl')[0]['error_category'] == 'invalid_output'
    run(inputs, tmp_path, provider)
    assert provider.calls == 52


def test_transient_retry_only(inputs, tmp_path):
    provider = ScriptedProvider([TransientProviderError('private-token'),
                                LLMResponse('{"predicted_state":"indeterminate"}')])
    run(inputs, tmp_path, provider)
    assert provider.calls == 53
    assert read(tmp_path/'responses.jsonl')[0]['attempt_count'] == 2
    assert read(tmp_path/'failures.jsonl')[0]['error_category'] == 'transient_provider_error'
    assert 'private-token' not in (tmp_path/'failures.jsonl').read_text()


@pytest.mark.parametrize('error,category,calls', [
    (PermanentProviderError('secret'), 'permanent_provider_error', 52),
    (RuntimeError('secret'), 'unexpected_provider_error', 52),
])
def test_nontransient_no_retry(inputs, tmp_path, error, category, calls):
    provider = ScriptedProvider([error])
    report = run(inputs, tmp_path, provider)
    assert provider.calls == calls
    assert read(tmp_path/'failures.jsonl')[0]['error_category'] == category
    assert 'secret' not in (tmp_path/'failures.jsonl').read_text()
    assert report['settings']['A-structured']['invalid_output_count'] == 1


def test_exhausted_retry_missing_prediction(inputs, tmp_path):
    provider = ScriptedProvider([TransientProviderError(), TransientProviderError()])
    report = run(inputs, tmp_path, provider)
    assert provider.calls == 53
    assert report['settings']['A-structured']['invalid_output_count'] == 1
    assert len(read(tmp_path/'failures.jsonl')) == 2


def test_resume_interrupted_journal(inputs, tmp_path):
    provider = MockProvider()
    run(inputs, tmp_path, provider)
    with (tmp_path/'responses.jsonl').open('ab') as stream:
        stream.write(b'{"partial":')
    run(inputs, tmp_path, provider)
    assert provider.calls == 52
    assert len(read(tmp_path/'responses.jsonl')) == 52
    assert (tmp_path/'responses.jsonl').read_bytes().endswith(b'\n')


def test_completed_corrupt_journal_rejected(tmp_path):
    file = tmp_path/'journal.jsonl'
    file.write_text('bad\n')
    with pytest.raises(ValueError):
        read_journal(file)


def test_external_response_import(inputs, tmp_path):
    provider = ExternalProvider('offline-provider', 'exact-model-revision')
    run(inputs, tmp_path/'out', provider, dry_run=True)
    key = read(tmp_path/'out/requests.jsonl')[0]['request_key']
    imported = tmp_path/'external.jsonl'
    imported.write_text(json.dumps({'request_key': key, 'raw_response': '{"predicted_state":"low_activity"}'})+'\n')
    report = run(inputs, tmp_path/'out', provider, responses_file=imported)
    assert len(read(tmp_path/'out/responses.jsonl')) == 1
    assert report['provider_attempt_count'] == 0
    assert report['settings']['A-structured']['invalid_output_count'] == 51
    with pytest.raises(ValueError):
        import_responses(imported, {'unknown'})


def test_configuration_change_refused(inputs, tmp_path):
    run(inputs, tmp_path, dry_run=True)
    with pytest.raises(ValueError):
        run(inputs, tmp_path, temperature=1, dry_run=True)


def test_fail_closed_before_any_call(inputs, tmp_path):
    import shutil
    unsafe = tmp_path/'unsafe'
    shutil.copytree(inputs, unsafe)
    records = read(unsafe/'condition_a_structured.jsonl')
    for record in records:
        record['task_input']['state'] = 'high_activity'
    (unsafe/'condition_a_structured.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    provider = MockProvider()
    with pytest.raises(ValueError):
        run(unsafe, tmp_path/'out', provider)
    assert provider.calls == 0


def test_no_secrets_in_outputs_and_input_immutable(inputs, tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret-do-not-copy')
    before = {p.name: digest(p.read_text()) for p in inputs.iterdir()}
    provider = ScriptedProvider([LLMResponse('{"predicted_state":"indeterminate"}',
                                           {'input_tokens': 2, 'authorization': 'test-secret-do-not-copy'})])
    run(inputs, tmp_path, provider)
    assert before == {p.name: digest(p.read_text()) for p in inputs.iterdir()}
    for file in tmp_path.rglob('*'):
        if file.is_file():
            assert 'test-secret-do-not-copy' not in file.read_text()


def test_output_lock_rejects_concurrent_writer(tmp_path):
    with output_lock(tmp_path):
        with pytest.raises(ValueError):
            with output_lock(tmp_path):
                pass


def test_paired_scoring_known_differences(inputs, tmp_path):
    from ros4hc_synthetic_scenario.llm_experiment import write_scores
    run_experiment(inputs, tmp_path, MockProvider(), dry_run=True)
    tasks = read(tmp_path/'requests.jsonl')
    manifest = json.loads((tmp_path/'experiment_manifest.json').read_text())
    expected = manifest['expected_states']
    responses = []
    for task in tasks:
        predicted = 'low_activity' if task['condition'] == 'a' else expected[task['sample_id']]
        responses.append({'request_key': task['request_key'],
                          'raw_response': json.dumps({'predicted_state': predicted})})
    report = write_scores(tmp_path, tasks, expected, responses, manifest)
    for rep in ('structured', 'ja'):
        assert report['settings']['B-'+rep]['accuracy'] == 1
        assert report['settings']['B-'+rep]['macro_f1_present_classes'] == 1
        assert report['paired_descriptive_differences']['B-A-'+rep]['accuracy'] == pytest.approx(1-16/52)
        assert report['paired_descriptive_differences']['C-B-'+rep]['accuracy'] == 0

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


# OpenAI transport is always replaced: these tests never connect or incur charges.
@pytest.fixture
def openai_transport(monkeypatch):
    from io import BytesIO
    from ros4hc_synthetic_scenario import llm_providers as providers
    monkeypatch.setenv('OPENAI_API_KEY', 'unit-test-credential')
    calls = []
    envelope = {'status': 'completed', 'output': [
        {'type': 'reasoning', 'summary': [{'text': 'do not persist reasoning'}]},
        {'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': '{"predicted_state":"indeterminate"}'}]}],
        'usage': {'input_tokens': 20, 'output_tokens': 8, 'total_tokens': 28,
                  'authorization': 'unit-test-credential', 'output_tokens_details': {'reasoning_tokens': 2}}}

    class Transport:
        failure = None

        def open(self, request, timeout):
            calls.append((request, timeout))
            if self.failure is not None:
                raise self.failure
            return BytesIO(json.dumps(envelope).encode())

    transport = Transport()
    monkeypatch.setattr(providers.http, 'build_opener', lambda *args: transport)
    return providers.OpenAIProvider('gpt-6-luna'), transport, calls, envelope


def test_openai_identifier_payload_text_usage(openai_transport):
    provider, _, calls, _ = openai_transport
    request = build_request({'hr': {'mean_hr_bpm': 82.0}, 'acc': {'activity_magnitude_mean': 1.0}})
    before = copy.deepcopy(request)
    result = provider.generate(request, {'reasoning_effort': 'none', 'temperature': 0, 'max_output_tokens': 128})
    assert provider.provider_identifier() == 'openai'
    assert provider.model_identifier() == 'gpt-6-luna'
    assert 'unit-test-credential' not in repr(provider) + repr(vars(provider))
    assert result.raw_response == '{"predicted_state":"indeterminate"}'
    assert result.usage == {'input_tokens': 20, 'output_tokens': 8, 'total_tokens': 28}
    wire, timeout = calls[0]
    payload = json.loads(wire.data)
    assert wire.full_url == 'https://api.openai.com/v1/responses' and wire.method == 'POST'
    assert timeout == 60
    assert payload == {'model': 'gpt-6-luna', 'store': False, 'input': request['prompt'],
                       'text': {'format': {'type': 'json_schema', 'strict': True,
                                           'name': 'phase5c_classification',
                                           'schema': {**request['output_schema'], 'properties': {
                                               'predicted_state': {'type': 'string', 'enum': list(LABELS)}}}}},
                       'temperature': 0, 'reasoning': {'effort': 'none'}, 'max_output_tokens': 128}
    assert request == before
    for token in ('ground_truth', 'sample_id', 'subject_id', 'evaluation', 'unit-test-credential'):
        assert token not in json.dumps(payload)


def test_openai_no_key_fail_closed(openai_transport, monkeypatch):
    provider, _, calls, _ = openai_transport
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(PermanentProviderError):
        provider.generate(build_request({'hr': {}}), {})
    assert not calls


@pytest.mark.parametrize('status', [408, 409, 429, 500, 502, 503, 599, 400, 401, 403, 404, 422, 302])
def test_openai_http_error_mapping_and_no_body_leak(openai_transport, status, capsys, caplog):
    from io import BytesIO
    from urllib.error import HTTPError
    import traceback
    provider, transport, _, _ = openai_transport
    body = BytesIO(b'private-api-error-body unit-test-credential')
    transport.failure = HTTPError('https://api.openai.com/v1/responses', status,
                                  'private-api-error-body unit-test-credential', {}, body)
    expected = TransientProviderError if status in (408, 409, 429) or status >= 500 else PermanentProviderError
    with pytest.raises(expected) as caught:
        provider.generate(build_request({'hr': {}}), {})
    rendered = ''.join(traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__))
    output = capsys.readouterr()
    assert body.closed
    for secret in ('private-api-error-body', 'unit-test-credential'):
        assert secret not in rendered + output.out + output.err + caplog.text


def test_openai_incomplete_read_is_transient_without_body_leak(openai_transport, monkeypatch, capsys, caplog):
    from http.client import IncompleteRead
    from io import BytesIO
    import traceback
    from ros4hc_synthetic_scenario import llm_providers as providers
    provider, _, _, _ = openai_transport

    class TruncatedResponse(BytesIO):
        def read(self, *args, **kwargs):
            raise IncompleteRead(b'private-response-body unit-test-credential', 100)

    response = TruncatedResponse()

    class Transport:
        def open(self, request, timeout):
            return response

    monkeypatch.setattr(providers.http, 'build_opener', lambda *args: Transport())
    with pytest.raises(TransientProviderError, match='OpenAI transport failed') as caught:
        provider.generate(build_request({'hr': {}}), {})
    assert response.closed
    rendered = ''.join(traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__))
    captured = capsys.readouterr()
    for forbidden in ('private-response-body', 'unit-test-credential', 'Authorization', 'IncompleteRead'):
        assert forbidden not in rendered + captured.out + captured.err + caplog.text


@pytest.mark.parametrize('failure', [TimeoutError('private transport body'),
                                   ConnectionError('private transport body')])
def test_openai_transport_error_mapping(openai_transport, failure):
    provider, transport, _, _ = openai_transport
    transport.failure = failure
    with pytest.raises(TransientProviderError, match='OpenAI transport failed'):
        provider.generate(build_request({'hr': {}}), {})


def test_openai_url_error_mapping(openai_transport):
    from urllib.error import URLError
    provider, transport, _, _ = openai_transport
    transport.failure = URLError('private transport body')
    with pytest.raises(TransientProviderError):
        provider.generate(build_request({'hr': {}}), {})


@pytest.mark.parametrize('decoding', [{'seed': 1}, {'temperature': float('nan')},
    {'temperature': 0, 'reasoning_effort': 'medium'}, {'max_output_tokens': 0},
    {'reasoning_effort': 'invented'}, {'authorization': 'private'}])
def test_openai_unsupported_decoding_not_silently_dropped(openai_transport, decoding):
    provider, _, calls, _ = openai_transport
    with pytest.raises(PermanentProviderError):
        provider.generate(build_request({'hr': {}}), decoding)
    assert not calls


@pytest.mark.parametrize('metadata', ['sample_id', 'subject_id', 'ground_truth', 'evaluation_metadata'])
def test_openai_rejects_evaluator_envelope(openai_transport, metadata):
    provider, _, calls, _ = openai_transport
    request = build_request({'hr': {}})
    request[metadata] = 'must-not-send'
    with pytest.raises(PermanentProviderError):
        provider.generate(request, {})
    assert not calls


@pytest.mark.parametrize('variant', ['incomplete', 'refusal', 'missing_text', 'credential_echo', 'error'])
def test_openai_invalid_envelope_never_persisted(openai_transport, variant):
    provider, _, _, envelope = openai_transport
    if variant in ('incomplete', 'error'):
        envelope['status'] = variant
        envelope['error'] = {'message': 'private-api-error-body'}
    elif variant == 'refusal':
        envelope['output'][1]['content'] = [{'type': 'refusal', 'refusal': 'private refusal'}]
    elif variant == 'missing_text':
        envelope['output'] = []
    else:
        envelope['output'][1]['content'][0]['text'] = 'unit-test-credential'
    with pytest.raises(PermanentProviderError):
        provider.generate(build_request({'hr': {}}), {})


def test_openai_runner_records_effective_decoding_and_sanitizes(inputs, tmp_path, openai_transport, capsys, caplog):
    from io import BytesIO
    from urllib.error import HTTPError
    provider, transport, calls, _ = openai_transport
    report = run(inputs, tmp_path/'success', provider, temperature=None,
                 reasoning_effort='none', max_output_tokens=128)
    decoding = {'reasoning_effort': 'none', 'max_output_tokens': 128}
    manifest = json.loads((tmp_path/'success/experiment_manifest.json').read_text())
    assert manifest['decoding'] == decoding and report['response_record_count'] == 52
    task = read(tmp_path/'success/requests.jsonl')[0]
    assert task['request_key'] == request_key('openai', 'gpt-6-luna', 'a', 'structured',
                                            task['sample_id'], task['request'], decoding)
    assert all('temperature' not in json.loads(w.data) for w, _ in calls)
    run(inputs, tmp_path/'success', provider, temperature=None, reasoning_effort='none', max_output_tokens=128)
    assert len(calls) == 52  # Existing resume path.
    transport.failure = HTTPError('https://api.openai.com/v1/responses', 401,
                                  'unit-test-credential', {}, BytesIO(b'private-api-error-body'))
    run(inputs, tmp_path/'failure', provider, temperature=None)
    for file in tmp_path.rglob('*'):
        if file.is_file():
            for forbidden in ('unit-test-credential', 'private-api-error-body', 'do not persist reasoning'):
                assert forbidden not in file.read_text()
    captured = capsys.readouterr()
    assert 'unit-test-credential' not in captured.out + captured.err + caplog.text


def test_openai_cli_requires_model(monkeypatch, tmp_path):
    from ros4hc_synthetic_scenario.llm_experiment import main
    monkeypatch.setattr('sys.argv', ['run_llm_experiment', '--provider', 'openai',
                                    '--input-dir', str(tmp_path), '--output', str(tmp_path/'out')])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2


@pytest.mark.parametrize('provider,expected', [('openai', None), ('mock', 0.0), ('external', 0.0)])
def test_cli_provider_temperature_defaults(monkeypatch, tmp_path, provider, expected):
    from ros4hc_synthetic_scenario import llm_experiment as experiment
    seen = {}
    def run_stub(*args, **kwargs):
        seen.update(kwargs)
        return {'settings': {}}
    monkeypatch.setattr(experiment, 'run_experiment', run_stub)
    monkeypatch.setattr('sys.argv', ['run_llm_experiment', '--provider', provider, '--model', 'exact-model',
                                    '--external-provider', 'offline', '--dry-run',
                                    '--input-dir', str(tmp_path), '--output', str(tmp_path/'out')])
    experiment.main()
    assert seen['temperature'] == expected

"""Phase 5B条件・漏洩・事実記述・採点を検証する。"""
import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.dataset import LABELS, PERTURBATIONS, export, generate, load_config, perturb
from ros4hc_synthetic_scenario.sensor_language import (
    CLASSIFICATION_PROMPT, EXPLANATION_PROMPT, HR_FIELDS, ACC_FIELDS, CONDITIONS,
    audit_input, build_inputs, export_inputs, format_ja, load_scenario, make_input, output_contract)
from ros4hc_synthetic_scenario.scoring import score_classification, validate_response


@pytest.fixture(scope='module')
def day():
    return generate(load_config(Path(__file__).parents[1]/'configs/daily_subject_001.json'))


@pytest.fixture(scope='module')
def inputs(day):
    return build_inputs(day)


@pytest.mark.parametrize('condition', CONDITIONS)
def test_clean_counts_leakage_and_ids(inputs, condition):
    exports, truth = inputs
    for representation in ('structured', 'ja'):
        records = exports[f'condition_{condition}_{representation}']
        assert len(records) == 288
        assert len({r['sample_id'] for r in records}) == 288
        for record in records:
            assert set(record) == {'sample_id', 'condition', 'task_input'}
            assert len(record['sample_id']) == 24
            int(record['sample_id'], 16)
            visible = record['task_input']
            audit_input(visible)
            serialized = json.dumps(visible, ensure_ascii=False)
            for state, ja in LABELS.items():
                assert state not in serialized and ja not in serialized
            assert all(s not in serialized for s in ('window_id', 'sample_id', 'subject_001', 'index'))
    assert {r['sample_id'] for r in records} == {r['sample_id'] for r in truth}


def test_a_b_projection(day):
    a = make_input(day['sensor_windows'], 204, 'a')
    b = make_input(day['sensor_windows'], 204, 'b')
    assert set(a) == {'hr', 'acc'}
    assert set(a['hr']) == set(HR_FIELDS)-{'hr_std_bpm'}
    assert set(a['acc']) == set(ACC_FIELDS)
    for key in ('hr', 'acc'):
        assert {k: v for k, v in b[key].items() if k != 'metadata'} == a[key]
        assert set(b[key]['metadata']) == {
            'observation_type', 'unit', 'unit_status', 'data_level', 'availability', 'quality', 'source'}
    assert 'metadata' not in json.dumps(a)
    for value in (a, b):
        assert '2026-' not in json.dumps(value) and 'time' not in json.dumps(value)


@pytest.mark.parametrize('index,history', [(0, 0), (1, 1), (5, 5), (6, 6), (210, 6)])
def test_c_d_history(day, index, history):
    c = make_input(day['sensor_windows'], index, 'c')
    d = make_input(day['sensor_windows'], index, 'd')
    assert c['available_history_windows'] == history
    assert c['available_history_minutes'] == history*5
    assert len(c['windows']) == history+1
    assert [w['relative_minutes'] for w in c['windows']] == list(range(-history*5, 1, 5))
    for position, (cw, dw) in enumerate(zip(c['windows'], d['windows'])):
        assert set(dw)-set(cw) == {'start_time', 'end_time'}
        assert {k:v for k,v in dw.items() if k not in ('start_time', 'end_time')} == cw
        assert dw['start_time'] == day['sensor_windows'][index-history+position]['start_time']
        assert dw['end_time'] == day['sensor_windows'][index-history+position]['end_time']
    assert '2026-' not in format_ja(c) and '2026-' in format_ja(d)


def test_history_configuration(day):
    assert len(make_input(day['sensor_windows'], 10, 'c', 1)['windows']) == 2
    assert len(make_input(day['sensor_windows'], 10, 'c', 0)['windows']) == 1
    for condition, history in [('x', 6), ('a', -1)]:
        with pytest.raises(ValueError):
            make_input(day['sensor_windows'], 10, condition, history)


@pytest.mark.parametrize('condition', CONDITIONS)
def test_ja_equivalent_facts_and_no_interpretation(day, condition):
    structured = make_input(day['sensor_windows'], 210, condition)
    ja = format_ja(structured)
    windows = structured.get('windows', [structured])
    for window in windows:
        sentence = format_ja({'hr': window['hr'], 'acc': window['acc']})
        assert sentence in ja
        for modality in ('hr', 'acc'):
            for key, value in window[modality].items():
                if key == 'metadata':
                    for field, content in value.items():
                        assert json.dumps(content, ensure_ascii=False, sort_keys=True) in sentence
                elif isinstance(value, (int, float)):
                    assert str(value) in sentence
                elif key == 'trend':
                    expected = {'increasing': '前窓より増加', 'decreasing': '前窓より減少',
                                'stable': '前窓との差は閾値以内', 'unavailable': '前窓との比較は利用できない'}
                    assert expected[value] in sentence
    assert all(text not in ja for text in ('運動中', '歩行している', '回復中', '診断'))
    audit_input(ja)


@pytest.mark.parametrize('kind', PERTURBATIONS)
def test_perturbed_inputs(day, kind):
    before = copy.deepcopy(day)
    changed = perturb(day, kind, 210)
    for condition in CONDITIONS:
        facts = make_input(changed['sensor_windows'], 210, condition)
        current = facts.get('windows', [facts])[-1]
        ja = format_ja(facts)
        audit_input(facts)
        audit_input(ja)
        assert kind not in json.dumps(facts) and kind not in ja
        for key, missing_kind, message in [('hr', 'missing_hr', '心拍データは利用できない。'),
                                            ('acc', 'missing_acc', '活動量データは利用できない。')]:
            if kind in (missing_kind, 'missing_both'):
                assert not any(k in current[key] for k in (HR_FIELDS if key == 'hr' else ACC_FIELDS))
                assert message in ja
                if condition == 'a':
                    assert current[key] == {}
        if kind == 'missing_both':
            current_ja = format_ja({'hr': current['hr'], 'acc': current['acc']})
            assert '心拍数の平均は' not in current_ja and '活動量指標の平均は' not in current_ja
        if kind == 'unknown_unit_metadata' and condition != 'a':
            assert '単位の確度は不明である。' in ja
    assert day == before


def test_build_deterministic_no_mutation(day, inputs):
    before = copy.deepcopy(day)
    assert build_inputs(day) == inputs
    assert day == before


def test_export_import_separation(tmp_path, day, inputs):
    before = copy.deepcopy(day)
    exports, truth = export_inputs(day, tmp_path)
    assert (exports, truth) == inputs
    for name, records in exports.items():
        loaded = [json.loads(line) for line in (tmp_path/(name+'.jsonl')).read_text().splitlines()]
        assert loaded == records
        assert 'ground_truth_state' not in (tmp_path/(name+'.jsonl')).read_text()
    assert json.loads((tmp_path/'evaluation_ground_truth.json').read_text()) == truth
    assert '288' in (tmp_path/'dataset_summary_ja.md').read_text()
    assert 'センサのみ' in (tmp_path/'dataset_summary_ja.md').read_text()
    assert day == before


def test_scenario_loader(tmp_path, day):
    export(day, tmp_path)
    assert load_scenario(tmp_path) == day
    bad = json.loads((tmp_path/'sensor_windows.json').read_text())
    bad[1]['start_time'] = bad[0]['start_time']
    (tmp_path/'sensor_windows.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        load_scenario(tmp_path)


def test_perturbation_evaluator_separation(day):
    before = copy.deepcopy(day)
    exports, targets = build_inputs(day, include_perturbations=True)
    assert len(targets) == 288*8
    assert all(len(rows) == 288*8 for rows in exports.values())
    assert len({t['sample_id'] for t in targets}) == 288*8
    for target in targets:
        if target['variant'] == 'missing_both':
            assert target['ground_truth_state'] == 'indeterminate'
        else:
            assert target['ground_truth_state'] == target['original_ground_truth_state']
    for name, records in exports.items():
        for record in records:
            audit_input(record['task_input'])
            assert all(kind not in json.dumps(record['task_input']) for kind in PERTURBATIONS)
    assert day == before


@pytest.mark.parametrize('task,prompt', [('classification', CLASSIFICATION_PROMPT),
                                        ('explanation', EXPLANATION_PROMPT)])
def test_prompts_and_contract(task, prompt):
    assert '{task_input}' in prompt
    rendered = prompt.format(task_input='観測値')
    assert '観測事実' in rendered and '診断' in rendered and '判断不能' in rendered
    assert 'JSON' in rendered and '文献引用' in rendered
    assert set(output_contract(task)['properties']['predicted_state']['enum']) == set(LABELS)
    assert all(label in rendered and ja in rendered for label, ja in LABELS.items())
    if task == 'explanation':
        assert all(key in rendered for key in ('evidence', 'uncertainty', 'unsupported_inferences_avoided'))


@pytest.mark.parametrize('leak', [
    {'state': 'opaque'}, {'acc': {'activity_level': 'high'}}, {'scenario_type': 'opaque'},
    {'hr': {'metadata': {'quality': {'state_label': 'opaque'}}}},
    {'acc': {'trend': 'walking'}}, {'acc': {'trend': 'medium'}},
    '回復中である。', '安静状態', 'low activity', 'rest_like', '高活動状態',
])
def test_leakage_negative_cases(leak):
    with pytest.raises(ValueError):
        audit_input(leak)


@pytest.mark.parametrize('response', [
    None, '', 'not json', '```json\n{}\n```', {}, [],
    {'predicted_state': 'unknown'}, {'predicted_state': ['high_activity']},
    {'predicted_state': 'high_activity', 'extra': 1},
    '{"predicted_state":"high_activity","predicted_state":"low_activity"}',
])
def test_invalid_classification(response):
    assert not validate_response(response)


def test_classification_scoring():
    expected = {'1': 'low_activity', '2': 'low_activity', '3': 'high_activity',
                '4': 'high_activity', '5': 'walking_like_activity'}
    predictions = {'1': {'predicted_state': 'low_activity'},
                   '2': '{"predicted_state":"high_activity"}',
                   '3': {'predicted_state': 'high_activity'}, '4': 'bad'}
    score = score_classification(expected, predictions)
    assert score['accuracy'] == 2/5
    assert score['invalid_output_count'] == 2
    assert score['per_class']['low_activity']['support'] == 2
    assert score['per_class']['high_activity']['predicted_count'] == 2
    assert score['per_class']['low_activity']['f1'] == pytest.approx(2/3)
    assert score['per_class']['high_activity']['f1'] == .5
    assert score['macro_f1_present_classes'] == pytest.approx((2/3+.5)/3)
    assert score['macro_f1_all_classes'] == pytest.approx((2/3+.5)/6)
    matrix = score['confusion_matrix']
    assert matrix['low_activity']['low_activity'] == 1
    assert matrix['low_activity']['high_activity'] == 1
    assert matrix['high_activity']['high_activity'] == 1
    assert sum(sum(row.values()) for row in matrix.values()) == 3
    assert score_classification({}, {})['accuracy'] == 0
    with pytest.raises(ValueError):
        score_classification(expected, {'unknown-id': {}})


def test_perfect_all_classes():
    expected = {str(i): state for i, state in enumerate(LABELS)}
    result = score_classification(expected, {sid: {'predicted_state': s} for sid, s in expected.items()})
    assert result['accuracy'] == result['macro_f1_present_classes'] == result['macro_f1_all_classes'] == 1
    assert result['invalid_output_count'] == 0


def test_explanation_validation():
    response = {'predicted_state': 'high_activity', 'evidence': ['平均心拍数138 bpm'],
                'uncertainty': '生活行動は特定できない', 'unsupported_inferences_avoided': []}
    assert validate_response(response, 'explanation')
    assert validate_response(json.dumps(response), 'explanation')
    for key, wrong in [('evidence', []), ('evidence', [1]), ('uncertainty', ''),
                       ('uncertainty', '  '), ('unsupported_inferences_avoided', 'text')]:
        assert not validate_response({**response, key: wrong}, 'explanation')
    for key in response:
        assert not validate_response({k:v for k,v in response.items() if k != key}, 'explanation')


def test_imported_perturbation_expected_label(tmp_path, day):
    changed = perturb(day, 'missing_both', 210)
    before = copy.deepcopy(changed)
    export(changed, tmp_path)
    imported = load_scenario(tmp_path)
    exports, targets = build_inputs(imported)
    assert targets[210]['ground_truth_state'] == 'indeterminate'
    assert targets[210]['original_ground_truth_state'] == 'post_activity_recovery'
    assert targets[210]['perturbations'][0]['kind'] == 'missing_both'
    assert exports['condition_a_structured'][210]['task_input'] == {'hr': {}, 'acc': {}}
    assert changed == before


def test_optional_hr_std(day):
    row = copy.deepcopy(day['sensor_windows'][0])
    row['hr']['hr_std_bpm'] = 1.25
    facts = make_input([row], 0, 'a')
    assert facts['hr']['hr_std_bpm'] == 1.25
    assert '心拍数の標準偏差は1.25 bpmである。' in format_ja(facts)


def test_projection_ignores_internal_target_fields(day):
    rows = copy.deepcopy(day['sensor_windows'])
    rows[210]['state'] = 'post_activity_recovery'
    rows[210]['acc']['activity_level'] = 'recovery'
    for condition in CONDITIONS:
        assert make_input(rows, 210, condition) == make_input(day['sensor_windows'], 210, condition)


def test_ground_truth_correspondence_rejected(day):
    broken = copy.deepcopy(day)
    broken['ground_truth'].pop()
    with pytest.raises(ValueError):
        build_inputs(broken)


def test_response_contract_display_labels():
    options = output_contract('classification')['properties']['predicted_state']['oneOf']
    assert [option['const'] for option in options] == list(LABELS)
    assert options[0]['title'] == 'A. 低活動状態'
    assert options[-1]['title'] == 'F. 判断不能'


def test_perfect_five_supported_classes(day):
    expected = {t['window_id']: t['ground_truth_state'] for t in day['ground_truth']}
    assert set(expected.values()) == set(LABELS)-{'indeterminate'}
    result = score_classification(expected, {sid: {'predicted_state': s} for sid, s in expected.items()})
    assert result['macro_f1_present_classes'] == 1.0
    assert result['macro_f1_all_classes'] == pytest.approx(5/6)
    assert result['per_class']['indeterminate']['support'] == 0
    assert set(result['confusion_matrix']) == set(LABELS)
    assert all(set(row) == set(LABELS) for row in result['confusion_matrix'].values())


def test_indeterminate_support_and_invalid_false_negatives():
    expected = {'1': 'low_activity', '2': 'indeterminate', '3': 'indeterminate'}
    predictions = {'1': {'predicted_state': 'low_activity'}, '2': 'bad'}
    result = score_classification(expected, predictions)
    assert result['invalid_output_count'] == 2
    assert result['per_class']['indeterminate']['support'] == 2
    assert result['per_class']['indeterminate']['invalid_count'] == 2
    assert result['per_class']['indeterminate']['recall'] == 0
    assert result['macro_f1_present_classes'] == .5
    assert result['macro_f1_all_classes'] == pytest.approx(1/6)
    assert result['accuracy'] == pytest.approx(1/3)


def test_empty_macro_f1():
    result = score_classification({}, {})
    assert result['macro_f1_present_classes'] == result['macro_f1_all_classes'] == 0


@pytest.mark.parametrize('prompt', [CLASSIFICATION_PROMPT, EXPLANATION_PROMPT])
def test_prompt_choices_are_sample_independent(day, prompt):
    prefixes = []
    for index in (0, 102, 204, 210):
        facts = make_input(day['sensor_windows'], index, 'a')
        audit_input(facts)
        text = json.dumps(facts, ensure_ascii=False)
        rendered = prompt.format(task_input=text)
        prefix, body = rendered.split('観測事実:\n', 1)
        assert json.loads(body) == facts
        assert all(state in prefix and ja in prefix for state, ja in LABELS.items())
        assert all(state not in body and ja not in body for state, ja in LABELS.items())
        assert 'ground_truth' not in rendered and 'subject_001' not in rendered
        prefixes.append(prefix)
    assert len(set(prefixes)) == 1

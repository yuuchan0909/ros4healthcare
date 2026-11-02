"""ハードウェア不要の研究fixture検証。"""
import copy
import csv
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.dataset import (
    LABELS, PERTURBATIONS, export, generate, load_config, perturb, temporal_context,
    validate_config, validate_dataset)


@pytest.fixture
def config():
    return load_config(Path(__file__).parents[1]/'configs/daily_subject_001.json')


@pytest.fixture
def data(config):
    return generate(config)


def test_day(data):
    r = data['sensor_windows']
    assert len(r) == 288
    assert r[0]['start_time'] == '2026-01-01T00:00:00+00:00'
    assert r[-1]['end_time'] == '2026-01-02T00:00:00+00:00'
    assert len({x['window_id'] for x in r}) == 288


def test_schedule(data, config):
    for s in config['schedule']:
        assert all(t['ground_truth_state'] == s['state'] for t in
                   data['ground_truth'][s['start_minute']//5:s['end_minute']//5])


def test_seed(config, data):
    assert generate(config) == data
    assert generate(config, 43) != data
    assert generate(config, 43)['ground_truth'] == data['ground_truth']


def test_ranges_and_recovery(config, data):
    validate_dataset(data, config)
    recovery = [r['hr']['mean_hr_bpm'] for r, t in zip(data['sensor_windows'], data['ground_truth'])
                if t['ground_truth_state'] == 'post_activity_recovery']
    assert recovery == [112, 105, 98, 91, 84, 77]


def test_export(tmp_path, data):
    export(data, tmp_path)
    visible = (tmp_path/'sensor_windows.json').read_text()
    assert json.loads(visible) == data['sensor_windows']
    assert 'ground_truth' not in visible
    assert all(s not in visible and ja not in visible for s, ja in LABELS.items())
    assert json.loads((tmp_path/'ground_truth.json').read_text()) == data['ground_truth']
    with (tmp_path/'daily_windows.csv').open(encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    assert len(rows) == 289 and rows[0][0] == '窓ID'
    csv_text = (tmp_path/'daily_windows.csv').read_text(encoding='utf-8-sig')
    assert all(s not in csv_text and ja not in csv_text for s, ja in LABELS.items())
    summary = (tmp_path/'scenario_summary_ja.md').read_text()
    assert '288窓' in summary and '低活動状態' in summary and '評価者専用' in summary


@pytest.mark.parametrize('previous,length', [(0, 1), (1, 2), (6, 7)])
def test_context(data, previous, length):
    context = temporal_context(data['sensor_windows'], 110, previous)
    assert len(context) == length and context[-1]['index'] == 110
    context[-1]['hr'].clear()
    assert data['sensor_windows'][110]['hr']
    assert len(temporal_context(data['sensor_windows'], 0, previous)) == 1


@pytest.mark.parametrize('kind', PERTURBATIONS)
def test_perturbations(data, kind, tmp_path):
    before = copy.deepcopy(data)
    out = perturb(data, kind, 110)
    r = out['sensor_windows'][110]
    assert data == before and out != data
    for key in ('hr', 'acc'):
        if kind == 'missing_both' or kind == 'missing_'+key:
            assert r[key]['metadata']['availability'] == 'missing'
            assert list(r[key]) == ['metadata']
    if kind == 'unknown_unit_metadata':
        assert all(r[k]['metadata']['unit'] == '' and
                   r[k]['metadata']['unit_status'] == 'UNIT_STATUS_UNKNOWN' for k in ('hr', 'acc'))
    if kind in ('hr_anomaly', 'sensor_contradiction'):
        assert r['hr']['mean_hr_bpm'] == 175
    if kind == 'acc_anomaly':
        assert r['acc']['activity_magnitude_mean'] == 20
    if kind == 'sensor_contradiction':
        assert r['acc']['activity_magnitude_mean'] == 0.01
    if kind == 'missing_both':
        assert out['perturbations'][0]['expected_state'] == 'indeterminate'
    assert out['ground_truth'] == data['ground_truth']
    export(out, tmp_path)
    visible = (tmp_path/'sensor_windows.json').read_text()
    assert kind not in visible and 'expected_state' not in visible


@pytest.mark.parametrize('damage', ['gap', 'duplicate', 'nan', 'subject', 'order'])
def test_validation_rejects(data, config, damage):
    r = data['sensor_windows']
    if damage == 'gap':
        r[1]['start_time'] = r[0]['start_time']
    elif damage == 'duplicate':
        r[1]['window_id'] = r[0]['window_id']
    elif damage == 'nan':
        r[1]['hr']['mean_hr_bpm'] = float('nan')
    elif damage == 'subject':
        r[1]['subject_id'] = 'wrong'
    else:
        r[1], r[2] = r[2], r[1]
    with pytest.raises((ValueError, AssertionError)):
        validate_dataset(data, config)


@pytest.mark.parametrize('damage', ['range', 'gap', 'recovery', 'duration'])
def test_invalid_config(config, damage):
    if damage == 'range':
        config['hr']['bounds'] = [180, 40]
    elif damage == 'gap':
        config['schedule'][1]['start_minute'] += 5
    elif damage == 'recovery':
        config['hr']['recovery_step'] = 100
    else:
        config['duration_minutes'] = 1435
    with pytest.raises(ValueError):
        validate_config(config)


# 状態名との完全一致だけでなく、別名キー・意味的同義語・カテゴリ追加を検出する。
FORBIDDEN_KEYS = {
    'ground_truth_state', 'state', 'state_label', 'state_label_ja', 'scenario_state',
    'scenario_type', 'activity_class', 'activity_level', 'recovery_phase', 'rest_like',
    '状態', '状態ラベル', '活動分類', '活動レベル', '回復段階',
}
FORBIDDEN_WORDS = {
    'low', 'light', 'high', 'walking', 'recovery', 'rest', 'resting', 'sedentary',
    'inactive', 'vigorous', 'indeterminate',
}
FORBIDDEN_JA = ('低活動', '軽活動', '歩行', '高活動', '回復', '安静', '休息', '判断不能')
VISIBLE_KEYS = {
    'window_id', 'subject_id', 'index', 'previous_window_id', 'start_time', 'end_time',
    'synthetic', 'hr', 'acc', 'metadata', 'mean_hr_bpm', 'min_hr_bpm', 'max_hr_bpm',
    'hr_change_bpm', 'activity_magnitude_mean', 'activity_magnitude_std', 'trend',
    'observation_type', 'unit', 'unit_status', 'data_level', 'source', 'time_semantics',
    'availability', 'quality', 'validity', 'quality_available', 'quality_value', 'quality_scale',
}


def audit_visible(value):
    import re
    if isinstance(value, dict):
        for key, item in value.items():
            assert key not in FORBIDDEN_KEYS and key in VISIBLE_KEYS, key
            audit_visible(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            audit_visible(item)
    elif isinstance(value, str):
        words = set(re.findall(r'[a-z]+', value.casefold()))
        assert not words.intersection(FORBIDDEN_WORDS), value
        assert not any(word in value for word in FORBIDDEN_JA), value


@pytest.mark.parametrize('kind', [None, *PERTURBATIONS])
def test_semantic_leakage_audit(data, tmp_path, kind):
    canonical = copy.deepcopy(data)
    candidate = data if kind is None else perturb(data, kind, 210)
    export(candidate, tmp_path)
    rows = json.loads((tmp_path/'sensor_windows.json').read_text())
    audit_visible(rows)
    # カテゴリ値の許可集合も固定し、同義語や新しいラベルの追加を検出する。
    for row in rows:
        assert row['acc'].get('trend', 'unavailable') in {
            'increasing', 'decreasing', 'stable', 'unavailable'}
        for modality in ('hr', 'acc'):
            meta = row[modality]['metadata']
            assert meta['availability'] in {'available', 'missing'}
            assert meta['observation_type'] == ('hr' if modality == 'hr' else 'activity')
            assert meta['source'] == ('synthetic_hr_generator' if modality == 'hr'
                                      else 'synthetic_activity_generator')
    with (tmp_path/'daily_windows.csv').open(encoding='utf-8-sig') as stream:
        table = list(csv.reader(stream))
    assert len(rows) == 288 and len(table) == 289
    assert table[0] == ['窓ID', '対象者ID', '開始時刻', '終了時刻', '平均心拍数(bpm)',
                        '最小心拍数(bpm)', '最大心拍数(bpm)', '心拍変化(bpm)',
                        '活動量平均(合成単位)', '活動量標準偏差', '活動量傾向',
                        'HR利用可否', 'ACC利用可否']
    audit_visible(table)
    assert data == canonical


@pytest.mark.parametrize('key', sorted(FORBIDDEN_KEYS))
def test_audit_rejects_forbidden_key(key):
    with pytest.raises(AssertionError):
        audit_visible({'acc': {'metadata': {key: 'opaque'}}})


@pytest.mark.parametrize('value', [
    'low_activity', 'light_activity', 'walking_like_activity', 'high_activity',
    'post_activity_recovery', 'recovery_phase', 'rest_like',
    'low', 'light', 'HIGH', 'walking', 'recovery', 'rest',
    '低活動状態', '軽活動状態', '歩行様活動', '高活動状態', '活動後回復状態',
    '安静状態', '休息状態', '判断不能',
])
def test_audit_rejects_semantic_label_alias(value):
    with pytest.raises(AssertionError):
        audit_visible({'acc': {'trend': value}})

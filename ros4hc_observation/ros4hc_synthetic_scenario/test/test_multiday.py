"""Phase 5E: 日程・分割・漏洩・数値対応の研究fixtureテスト。"""
import copy
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.dataset import LABELS
from ros4hc_synthetic_scenario.multiday import generate_multiday, export_multiday, load_multiday, PROFILE_RANGES
from ros4hc_synthetic_scenario.multiday_eval import (assign_splits, validate_split_mapping,
    trajectories_for_days, diagnostics, export_research, group_key)
from ros4hc_synthetic_scenario.trajectory import audit_model_input
from ros4hc_synthetic_scenario.world_model_eval import evaluate
from ros4hc_synthetic_scenario.world_model_interface import PersistenceBaseline


@pytest.fixture(scope='module')
def data():
    return generate_multiday()


@pytest.fixture(scope='module')
def samples(data):
    return trajectories_for_days(data['subject_days'])


def test_default_scale(data):
    assert len(data['profiles']) == 4
    counts = Counter(d['subject_id'] for d in data['subject_days'])
    assert set(counts.values()) == {7}
    assert len(data['subject_days']) == 28
    assert sum(len(d['sensor_windows']) for d in data['subject_days']) == 8064
    assert all(len(d['sensor_windows']) == 288 for d in data['subject_days'])


def test_global_ids(data):
    for field, id_field in [('sensor_windows', 'window_id'), ('events', 'event_id')]:
        values = [r[id_field] for d in data['subject_days'] for r in d[field]]
        assert len(values) == len(set(values))
        assert not any(label in v for v in values for label in LABELS)


def test_seeds(data):
    assert generate_multiday() == data
    other = generate_multiday(seed=43)
    assert other != data
    assert [d['schedule'] for d in other['subject_days']] != [d['schedule'] for d in data['subject_days']]


def test_profiles(data):
    for field, (lo, hi) in PROFILE_RANGES.items():
        vals = [p[field] for p in data['profiles']]
        assert len(set(vals)) == 4
        assert all(lo <= v <= hi for v in vals)
    assert all(set(p) == {'subject_id', *PROFILE_RANGES} for p in data['profiles'])


def test_schedules(data):
    for d in data['subject_days']:
        blocks = d['schedule']
        assert blocks[0]['start_index'] == 0 and blocks[-1]['end_index'] == 288
        assert all(a['end_index'] == b['start_index'] for a, b in zip(blocks, blocks[1:]))
        assert all(b['end_index'] > b['start_index'] for b in blocks)
        assert 'indeterminate' not in {t['ground_truth_state'] for t in d['ground_truth']}
        rows = d['sensor_windows']
        assert all(datetime.fromisoformat(r['end_time'])-datetime.fromisoformat(r['start_time']) == timedelta(minutes=5) for r in rows)
        assert all(a['end_time'] == b['start_time'] for a, b in zip(rows, rows[1:]))
    assert len({json.dumps(d['schedule']) for d in data['subject_days']}) == 28


def test_overlap_and_simple(data, samples):
    diag = diagnostics(data['subject_days'], samples)
    def overlap(diag, states, field):
        return next(x['range_overlap_width'] for x in diag['range_overlaps'] if set(x['states']) == set(states) and x['field'] == field)
    for pair in [('light_activity', 'walking_like_activity'), ('walking_like_activity', 'high_activity'), ('post_activity_recovery', 'walking_like_activity')]:
        assert overlap(diag, pair, 'hr') > 0
    simple = generate_multiday(mode='simple')
    sdiag = diagnostics(simple['subject_days'], [])
    assert overlap(sdiag, ('light_activity', 'high_activity'), 'hr') < overlap(diag, ('light_activity', 'high_activity'), 'hr')
    assert overlap(sdiag, ('light_activity', 'high_activity'), 'acc') < overlap(diag, ('light_activity', 'high_activity'), 'acc')


def test_ramps_and_recovery():
    data = generate_multiday(subjects=1, days=2, mode='simple')
    for d in data['subject_days']:
        rows = d['sensor_windows']
        recovery = [b for b in d['schedule'] if b['state'] == 'post_activity_recovery']
        for block in recovery:
            means = [r['hr']['mean_hr_bpm'] for r in rows[block['start_index']:block['end_index']]]
            assert all(a > b for a, b in zip(means, means[1:]))
        for block in d['schedule']:
            if block['state'] == 'high_activity':
                i = block['start_index']
                assert rows[i-1]['acc']['activity_magnitude_mean'] < rows[i]['acc']['activity_magnitude_mean'] < rows[i+1]['acc']['activity_magnitude_mean']


def test_events(data):
    for d in data['subject_days']:
        truth = d['ground_truth']
        expected = []
        for i in range(1, 288):
            prev, curr = truth[i-1]['ground_truth_state'], truth[i]['ground_truth_state']
            if prev == curr:
                continue
            if prev in ('walking_like_activity', 'high_activity'):
                expected.append((i, 'activity_ended'))
            if prev == 'post_activity_recovery':
                expected.append((i, 'recovery_completed'))
            if curr in ('walking_like_activity', 'high_activity'):
                expected.append((i, 'activity_started'))
            if curr == 'post_activity_recovery':
                expected.append((i, 'recovery_started'))
        assert [(e['index'], e['event_type']) for e in d['events']] == expected
        assert all(e['synthetic_annotation'] and e['window_id'] == truth[e['index']]['window_id'] for e in d['events'])


def test_neutral_input(samples):
    for s in samples:
        audit_model_input(s.model_input)
        for k in ('action', 'event', 'context', 'intervention'):
            assert s.model_input[k] == {'known': False, 'value': None}
        text = json.dumps(s.model_input)
        for forbidden in ('activity_started', 'activity_ended', 'recovery_started', 'recovery_completed', 'split', 'scenario', 'transition', 'future', 'ground_truth', *LABELS):
            assert forbidden not in text


@pytest.mark.parametrize('field', ['future_state', 'current_state', 'split', 'scenario', 'transition_type', 'event_annotation', 'future_observations'])
def test_injection_rejected(samples, field):
    inp = copy.deepcopy(samples[0].model_input)
    inp[field] = 'opaque'
    with pytest.raises(ValueError):
        audit_model_input(inp)


@pytest.mark.parametrize('h', [5, 15, 30])
def test_horizons_and_day_boundary(data, samples, h):
    by_key = {group_key(d): d for d in data['subject_days']}
    assert len(samples) == 7896
    for s in samples:
        t = s.evaluator_target
        day = by_key[(t['subject_id'], t['day_id'])]
        i = t['source_current_index']
        assert i <= 281
        row = day['sensor_windows'][i+h//5]
        expected = next(x for x in t['future_targets'] if x['horizon_minutes'] == h)
        assert row['start_time'][:10] == t['day_id']
        assert expected['mean_hr_bpm'] == row['hr']['mean_hr_bpm']
        assert expected['activity_magnitude_mean'] == row['acc']['activity_magnitude_mean']
        assert expected['future_state'] == day['ground_truth'][i+h//5]['ground_truth_state']
        first = max(0, i-6)
        assert s.model_input['history'][0]['hr']['mean_hr_bpm'] == day['sensor_windows'][first]['hr']['mean_hr_bpm']


def test_splits(data, samples):
    days = data['subject_days']
    mapping = assign_splits(days)
    validate_split_mapping(days, mapping)
    assert Counter(mapping.values()) == {'train': 15, 'validation': 6, 'test': 7}
    # 各元窓がどのsplitから参照されるか調べ、履歴・未来両方の重複を監査。
    references = {}
    for s in samples:
        t = s.evaluator_target
        key = (t['subject_id'], t['day_id'])
        split = mapping[key]
        i = t['source_current_index']
        for index in (*range(max(0, i-6), i+1), i+1, i+3, i+6):
            references.setdefault((*key, index), set()).add(split)
    assert all(len(v) == 1 for v in references.values())
    assert {k[0] for k, v in mapping.items() if v == 'test'} == {'subject_004'}


@pytest.mark.parametrize('strategy,field', [('leave-subject-out','subject_id'), ('leave-day-out','calendar_day'), ('scenario-day-group','scenario_id')])
def test_split_utilities(data, strategy, field):
    days = data['subject_days']
    values = sorted({d[field] for d in days})
    mapping = assign_splits(days, strategy, (values[-2],), (values[-1],))
    assert all(mapping[group_key(d)] == ('test' if d[field] == values[-1] else 'validation' if d[field] == values[-2] else 'train') for d in days)
    with pytest.raises(ValueError):
        assign_splits(days, strategy, (values[0],), (values[0],))


def test_split_rejects(data):
    days = data['subject_days']
    with pytest.raises(ValueError):
        assign_splits(days+days[:1])
    with pytest.raises(ValueError):
        validate_split_mapping(days, {})


def test_transition_matrix(data, samples):
    diag = diagnostics(data['subject_days'], samples)
    for h in (5, 15, 30):
        matrix = diag['transition_matrices'][str(h)]
        assert sum(sum(r.values()) for r in matrix.values()) == 7896
        expected = Counter((s.evaluator_target['current_state'], next(t['future_state'] for t in s.evaluator_target['future_targets'] if t['horizon_minutes'] == h)) for s in samples)
        assert all(matrix[a][b] == expected[a,b] for a in LABELS for b in LABELS)
        assert sum(matrix[a][b] for a in LABELS for b in LABELS if a != b) == diag['transition_counts'][str(h)]['transition']


def test_baseline(samples):
    _, scores = evaluate(samples[:282], PersistenceBaseline())
    for h in ('5','15','30'):
        assert scores[h]['all']['sample_count'] == 282
        assert scores[h]['same-state']['sample_count'] + scores[h]['transition']['sample_count'] == 282
        assert scores[h]['all']['mean_hr_bpm']['mae'] >= 0
        assert scores[h]['all']['activity_magnitude_mean']['rmse'] >= 0
        assert scores[h]['all']['state_classification']['count'] == 0


def test_export_and_split_counts(data, tmp_path):
    export_multiday(data, tmp_path/'dataset')
    loaded = load_multiday(tmp_path/'dataset')
    assert loaded['profiles'] == data['profiles']
    assert len((tmp_path/'dataset'/'all_sensor_windows.jsonl').read_text().splitlines()) == 8064
    samples, report, scores = export_research(loaded, tmp_path/'trajectories')
    for split, n in [('train',15), ('validation',6), ('test',7)]:
        diag = report['splits'][split]
        assert len(diag['subject_days']) == diag['subject_day_count'] == n
        assert diag['windows'] == n*288
        assert diag['trajectories'] == n*282
        assert sum(diag['state_counts'].values()) == n*288
        assert scores[split]['5']['all']['sample_count'] == n*282
    assert scores['overall']['30']['all']['sample_count'] == 7896
    assert '1日synthetic fixtureのみ' not in (tmp_path/'trajectories'/'dataset_summary_ja.md').read_text()
    assert 'subject_distributions' in json.loads((tmp_path/'trajectories'/'dataset_diagnostics.json').read_text())


@pytest.mark.parametrize('subjects,days,mode', [(0,7,'simple'),(4,0,'simple'),(1,1,'invalid')])
def test_invalid(subjects, days, mode):
    with pytest.raises(ValueError):
        generate_multiday(subjects, days, mode=mode)


def test_no_training_dependency():
    root = Path(__file__).parents[1]/'ros4hc_synthetic_scenario'
    for name in ('multiday.py','multiday_eval.py'):
        text = (root/name).read_text()
        assert all(x not in text for x in ('import torch','import tensorflow','import sklearn','import requests'))


def test_configurable_ranges():
    config = {'ramp_windows': [3,3], 'recovery_windows': [5,5],
              'schedule_ranges': [['walking_like_activity', 100,110,10,12], ['high_activity',200,210,6,8]]}
    data = generate_multiday(1,2,config=config)
    assert all(d['dynamics']['ramp_windows'] == 3 for d in data['subject_days'])
    assert data['generation_settings']['recovery_windows'] == [5,5]
    for d in data['subject_days']:
        assert all(b['end_index']-b['start_index'] == 5 for b in d['schedule'] if b['state'] == 'post_activity_recovery')


@pytest.mark.parametrize('config', [
    {'unknown': 2}, {'ramp_windows': [0,2]}, {'recovery_windows': [3,30]},
    {'day_intensity': [-1,1]}, {'day_hr_offset': [-100,1]},
    {'schedule_ranges': [['high_activity',280,285,10,15]]},
    {'schedule_ranges': [['high_activity',100,110,10,15], ['light_activity',115,120,10,20]]},
    {'profile_ranges': {**PROFILE_RANGES, 'rest_hr': [0,200]}},
])
def test_invalid_config(config):
    with pytest.raises(ValueError):
        generate_multiday(1,1,config=config)


def test_observation_bounds(data):
    for d in data['subject_days']:
        for r in d['sensor_windows']:
            assert 40 <= r['hr']['min_hr_bpm'] <= r['hr']['mean_hr_bpm'] <= r['hr']['max_hr_bpm'] <= 180
            assert 0 <= r['acc']['activity_magnitude_mean'] <= 2.5


def test_duplicate_day_rejected(data):
    with pytest.raises(ValueError):
        trajectories_for_days(data['subject_days'][:1]*2)

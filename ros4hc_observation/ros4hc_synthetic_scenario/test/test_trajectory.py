"""Phase 5D: 漏洩防止・軌跡対応・予測契約の検証。"""
import copy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.dataset import generate, load_config, export
from ros4hc_synthetic_scenario.trajectory import build_trajectories, audit_model_input, export_trajectories
from ros4hc_synthetic_scenario.world_model_interface import Prediction, PersistenceBaseline, WorldModelAdapter
from ros4hc_synthetic_scenario.world_model_eval import evaluate


@pytest.fixture
def data():
    return generate(load_config(Path(__file__).parents[1]/'configs/daily_subject_001.json'))


@pytest.fixture
def samples(data):
    return build_trajectories(data)


def test_count_and_end(samples):
    assert len(samples) == 282
    assert samples[0].evaluator_target['source_current_index'] == 0
    assert samples[-1].evaluator_target['source_current_index'] == 281


def test_history(samples):
    assert len(samples[0].model_input['history']) == 1
    for s in samples[6:]:
        assert s.model_input['history_window_count'] == 7
        assert s.model_input['history_minutes'] == 30
        assert s.model_input['current_index'] == 6
        assert [w['relative_minutes'] for w in s.model_input['history']] == list(range(-30, 1, 5))


@pytest.mark.parametrize('h', [5, 15, 30])
def test_targets(samples, data, h):
    for i, s in enumerate(samples):
        target = next(t for t in s.evaluator_target['future_targets'] if t['horizon_minutes'] == h)
        row = data['sensor_windows'][i+h//5]
        assert target['mean_hr_bpm'] == row['hr']['mean_hr_bpm']
        assert target['activity_magnitude_mean'] == row['acc']['activity_magnitude_mean']
        assert target['future_state'] == data['ground_truth'][i+h//5]['ground_truth_state']


def test_input_separation(samples, data):
    for i, s in enumerate(samples):
        inp = s.model_input
        audit_model_input(inp)
        assert len(s.trajectory_id) == 64 and all(c in '0123456789abcdef' for c in s.trajectory_id)
        assert all(inp[k] == {'known': False, 'value': None} for k in ('action', 'event', 'context', 'intervention'))
        assert inp['history'][-1]['hr'] == {k: v for k, v in data['sensor_windows'][i]['hr'].items() if k != 'metadata'}
        assert all(w['relative_minutes'] <= 0 for w in inp['history'])
        for forbidden in ('ground_truth', 'future_state', 'transition', 'scenario', 'subject', 'timestamp', 'target'):
            assert forbidden not in json.dumps(inp)


@pytest.mark.parametrize('key', ['current_state', 'future_state', 'transition_category', 'scenario_name', 'future_observations', 'future_timestamp', 'trajectory_id', 'subject_id'])
def test_leakage_rejects(samples, key):
    inp = copy.deepcopy(samples[10].model_input)
    inp[key] = 'hidden'
    with pytest.raises(ValueError):
        audit_model_input(inp)


@pytest.mark.parametrize('damage', ['future', 'nested', 'metadata', 'label', 'unknown'])
def test_nested_leakage(samples, damage):
    inp = copy.deepcopy(samples[10].model_input)
    if damage == 'future':
        inp['history'][-1]['relative_minutes'] = 5
    elif damage == 'nested':
        inp['history'][0]['hr']['future_mean_hr_bpm'] = 100
    elif damage == 'metadata':
        inp['history'][0]['hr']['metadata'] = {'scenario': 'opaque'}
    elif damage == 'label':
        inp['history'][0]['acc']['trend'] = 'walking_like_activity'
    else:
        inp['action'] = {'known': True, 'value': 'walking'}
    with pytest.raises(ValueError):
        audit_model_input(inp)


def test_determinism_and_export(samples, data, tmp_path):
    before = copy.deepcopy(data)
    assert samples == build_trajectories(data)
    export_trajectories(samples, tmp_path)
    first = (tmp_path/'trajectory_inputs.jsonl').read_bytes()
    export_trajectories(samples, tmp_path)
    assert first == (tmp_path/'trajectory_inputs.jsonl').read_bytes()
    assert data == before
    manifest = json.loads((tmp_path/'trajectory_manifest.json').read_text())
    assert manifest['split_group_fields'] == ['subject_id', 'day_id', 'scenario_id']
    assert manifest['random_window_split_recommended'] is False


def test_transitions(samples):
    categories = set()
    for s in samples:
        for t in s.evaluator_target['future_targets']:
            expected = 'same-state' if s.evaluator_target['current_state'] == t['future_state'] else 'transition'
            assert t['transition_category'] == expected
            categories.add(expected)
    assert categories == {'same-state', 'transition'}


def test_persistence(samples):
    adapter = PersistenceBaseline()
    assert isinstance(adapter, WorldModelAdapter)
    for s in samples:
        p = adapter.predict(s.model_input, (5, 15, 30))
        assert p.predicted_state is p.latent_state is p.uncertainty is None
        for h in (5, 15, 30):
            assert p.predicted_observations[h]['mean_hr_bpm'] == s.model_input['history'][-1]['hr']['mean_hr_bpm']
            assert p.predicted_observations[h]['activity_magnitude_mean'] == s.model_input['history'][-1]['acc']['activity_magnitude_mean']


def test_metrics(samples):
    class Fixed:
        def predict(self, model_input, horizons_minutes):
            return Prediction(predicted_observations={h: {'mean_hr_bpm': 10, 'activity_magnitude_mean': 2} for h in horizons_minutes})
    selected = []
    for s, hr, acc, category in zip(samples[:2], (7, 6), (1, -1), ('same-state', 'transition')):
        target = copy.deepcopy(s.evaluator_target)
        for t in target['future_targets']:
            t.update(mean_hr_bpm=hr, activity_magnitude_mean=acc, transition_category=category)
        selected.append(replace(s, evaluator_target=target))
    _, scores = evaluate(selected, Fixed())
    assert set(scores) == {'5', '15', '30'}
    for groups in scores.values():
        assert groups['all']['mean_hr_bpm']['mae'] == 3.5
        assert groups['all']['mean_hr_bpm']['rmse'] == pytest.approx((12.5)**0.5)
        assert groups['all']['activity_magnitude_mean']['mae'] == 2
        assert groups['all']['activity_magnitude_mean']['rmse'] == pytest.approx(5**0.5)
        assert groups['same-state']['mean_hr_bpm']['mae'] == 3
        assert groups['transition']['mean_hr_bpm']['mae'] == 4
        assert groups['all']['state_classification']['count'] == 0


def test_optional_outputs(samples):
    class Latent:
        def predict(self, model_input, horizons_minutes):
            return Prediction(latent_state=[1, 2])
    _, scores = evaluate(samples[:1], Latent())
    assert scores['5']['all']['mean_hr_bpm'] == {'count': 0, 'missing_count': 1, 'mae': None, 'rmse': None}
    class State:
        def predict(self, model_input, horizons_minutes):
            return Prediction(predicted_state={h: 'low_activity' for h in horizons_minutes})
    _, scores = evaluate(samples[:1], State())
    assert scores['5']['all']['state_classification']['accuracy'] == 1


def test_multiday_and_subject(data):
    second = copy.deepcopy(data)
    for row, truth in zip(second['sensor_windows'], second['ground_truth']):
        row['window_id'] += '_other'
        truth['window_id'] = row['window_id']
        for k in ('start_time', 'end_time'):
            row[k] = (datetime.fromisoformat(row[k])+timedelta(days=1)).isoformat()
    combined = {k: data[k]+second[k] for k in data}
    samples = build_trajectories(combined)
    assert len(samples) == 564
    assert samples[282].model_input['history_window_count'] == 1
    for row in second['sensor_windows']:
        row['subject_id'] = 'other'
    assert len(build_trajectories({k: data[k]+second[k] for k in data})) == 564


@pytest.mark.parametrize('horizons', [(), (0,), (7,), (5, 5), (-5,)])
def test_invalid_horizons(data, horizons):
    with pytest.raises(ValueError):
        build_trajectories(data, horizons=horizons)


def test_bad_order(data):
    data['sensor_windows'][1], data['sensor_windows'][2] = data['sensor_windows'][2], data['sensor_windows'][1]
    with pytest.raises(ValueError):
        build_trajectories(data)


def test_canonical_bytes_unchanged(data, tmp_path):
    export(data, tmp_path/'before')
    build_trajectories(data, include_metadata=True)
    export(data, tmp_path/'after')
    for p in (tmp_path/'before').iterdir():
        assert p.read_bytes() == (tmp_path/'after'/p.name).read_bytes()


def test_no_learned_dependency():
    root = Path(__file__).parents[1]
    for name in ('trajectory.py', 'world_model_interface.py', 'world_model_eval.py'):
        text = (root/'ros4hc_synthetic_scenario'/name).read_text()
        assert all(word not in text for word in ('import torch', 'import tensorflow', 'import sklearn', 'import requests'))

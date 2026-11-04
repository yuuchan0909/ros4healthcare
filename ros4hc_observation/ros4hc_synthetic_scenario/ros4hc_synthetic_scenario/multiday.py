"""Phase 5E: CPUのみの制御された合成fixture。医学的モデルではない。"""
import argparse
import copy
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random

from .dataset import metadata
from .trajectory import write_json, write_jsonl

BOUNDS = {'hr': [40, 180], 'acc': [0, 2.5]}
PROFILE_RANGES = {'rest_hr': [56, 72], 'response_scale': [0.8, 1.2],
                  'activity_scale': [0.8, 1.2], 'recovery_rate': [0.12, 0.35],
                  'jitter': [1, 3]}
# 各区間は別々の開始領域を持ち、最大durationでも重ならない。
SCHEDULE_RANGES = [
    ('light_activity', 72, 84, 12, 24),
    ('walking_like_activity', 108, 120, 8, 16),
    ('light_activity', 150, 154, 24, 30),
    ('walking_like_activity', 186, 190, 8, 12),
    ('high_activity', 216, 228, 6, 12),
    ('light_activity', 264, 270, 6, 12)]


def validate_settings(settings):
    if set(settings) != {'profile_ranges', 'schedule_ranges', 'ramp_windows', 'recovery_windows', 'day_hr_offset', 'day_intensity'}:
        raise ValueError('未定義の生成設定です')
    if set(settings['profile_ranges']) != set(PROFILE_RANGES):
        raise ValueError('subjectパラメータ設定が不正です')
    for key, pair in settings['profile_ranges'].items():
        lo, hi = PROFILE_RANGES[key]
        if len(pair) != 2 or not all(type(x) in (int, float) and math.isfinite(x) for x in pair) or not lo <= pair[0] <= pair[1] <= hi:
            raise ValueError('profile範囲は宣言済みsynthetic bounds内で指定してください')
    for key in ('ramp_windows', 'recovery_windows'):
        pair = settings[key]
        if len(pair) != 2 or any(type(x) is not int for x in pair) or not 1 <= pair[0] <= pair[1] <= 24:
            raise ValueError('窓数設定が不正です')
    for key in ('day_hr_offset', 'day_intensity'):
        pair = settings[key]
        if len(pair) != 2 or not all(type(x) in (int, float) and math.isfinite(x) for x in pair) or pair[0] > pair[1]:
            raise ValueError('日変動設定が不正です')
    if not -10 <= settings['day_hr_offset'][0] <= settings['day_hr_offset'][1] <= 10 or not 0.5 <= settings['day_intensity'][0] <= settings['day_intensity'][1] <= 1.5:
        raise ValueError('日変動のsynthetic bounds外です')
    end = 0
    for block in settings['schedule_ranges']:
        state, lo, hi, dmin, dmax = block
        if state not in ('light_activity', 'walking_like_activity', 'high_activity') or any(type(x) is not int for x in block[1:]) or not end <= lo <= hi or not 1 <= dmin <= dmax:
            raise ValueError('日程範囲が重複または不正です')
        end = hi+dmax+(settings['recovery_windows'][1] if state in ('walking_like_activity', 'high_activity') else 0)
        if end > 287:
            raise ValueError('日末までに活動・回復が完了する必要があります')


def generate_multiday(subjects=4, days=7, seed=42, mode='overlapping', config=None):
    if type(subjects) is not int or type(days) is not int or subjects < 1 or days < 1 or mode not in ('simple', 'overlapping'):
        raise ValueError('subject数・日数は正整数、modeはsimple/overlappingです')
    settings = {'profile_ranges': copy.deepcopy(PROFILE_RANGES), 'schedule_ranges': copy.deepcopy(SCHEDULE_RANGES),
                'ramp_windows': [2, 5], 'recovery_windows': [4, 10],
                'day_hr_offset': [-4, 4], 'day_intensity': [0.75, 1.25]}
    if config is not None:
        if not set(config) <= set(settings):
            raise ValueError('未定義の生成設定です')
        settings.update(copy.deepcopy(config))
    validate_settings(settings)
    rng = random.Random(seed)
    profiles, subject_days = [], []
    for subject_no in range(1, subjects+1):
        sid = f'subject_{subject_no:03d}'
        profile = {'subject_id': sid, **{k: round(rng.uniform(*v), 6) for k, v in settings['profile_ranges'].items()}}
        profiles.append(profile)
        for day_no in range(1, days+1):
            day_id = f'day_{day_no:03d}'
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(days=day_no-1)
            states = ['low_activity']*288
            for state, lo, hi, dmin, dmax in settings['schedule_ranges']:
                begin, duration = rng.randint(lo, hi), rng.randint(dmin, dmax)
                states[begin:begin+duration] = [state]*duration
                if state in ('walking_like_activity', 'high_activity'):
                    recovery = rng.randint(*settings['recovery_windows'])
                    states[begin+duration:begin+duration+recovery] = ['post_activity_recovery']*recovery
            schedule = []
            for i, state in enumerate(states):
                if not i or state != states[i-1]:
                    schedule.append({'start_index': i, 'end_index': i+1, 'state': state})
                else:
                    schedule[-1]['end_index'] = i+1
            rows, truth, events = [], [], []
            previous_hr = previous_acc = None
            hr = profile['rest_hr']
            acc = 0.04
            ramp = rng.randint(*settings['ramp_windows'])
            # 日ごとの振幅・基準値変化。overlappingでは区間単位の振幅も揺らす。
            day_offset = rng.uniform(*settings['day_hr_offset'])
            intensity = rng.uniform(*settings['day_intensity'])
            response = 0
            activity = 0
            for i, state in enumerate(states):
                wid = f'{sid}_{day_id}_{i:03d}'
                changed = i == 0 or states[i-1] != state
                if changed:
                    hr_delta = {'low_activity': 0, 'light_activity': 14, 'walking_like_activity': 25,
                                'high_activity': 38, 'post_activity_recovery': 0}
                    acc_level = {'low_activity': 0.04, 'light_activity': 0.20,
                                 'walking_like_activity': 0.48, 'high_activity': 0.85,
                                 'post_activity_recovery': 0.08}
                    if mode == 'simple':
                        hr_delta.update(light_activity=20, walking_like_activity=45, high_activity=80)
                        acc_level.update(light_activity=0.25, walking_like_activity=0.85, high_activity=1.8)
                    spread = 14 if mode == 'overlapping' else 1
                    response = hr_delta[state]+(rng.uniform(-spread, spread) if state not in ('low_activity', 'post_activity_recovery') else 0)
                    activity = acc_level[state]*rng.uniform(0.55, 1.45) if mode == 'overlapping' else acc_level[state]
                    previous_state = states[i-1] if i else None
                    names = []
                    if previous_state in ('walking_like_activity', 'high_activity'):
                        names.append('activity_ended')
                    if previous_state == 'post_activity_recovery':
                        names.append('recovery_completed')
                    if state in ('walking_like_activity', 'high_activity'):
                        names.append('activity_started')
                    if state == 'post_activity_recovery':
                        names.append('recovery_started')
                    for name in names:
                        events.append({'event_id': f'{sid}_{day_id}_e{len(events):03d}', 'window_id': wid,
                                       'subject_id': sid, 'day_id': day_id, 'index': i,
                                       'event_type': name, 'synthetic_annotation': True})
                target_hr = profile['rest_hr']+day_offset+response*profile['response_scale']*intensity
                target_acc = activity*profile['activity_scale']*intensity
                alpha = profile['recovery_rate'] if state == 'post_activity_recovery' else 1/ramp
                hr += alpha*(target_hr-hr)
                acc += (1/ramp)*(target_acc-acc)
                jitter = profile['jitter']*(2.5 if mode == 'overlapping' else 0.3)
                mean = round(max(40, min(180, hr+rng.uniform(-jitter, jitter))), 4)
                magnitude = round(max(0, min(2.5, acc+rng.uniform(-0.04, 0.04)*(1 if mode == 'overlapping' else 0.1))), 4)
                rows.append({'window_id': wid, 'subject_id': sid, 'index': i,
                    'previous_window_id': rows[-1]['window_id'] if rows else None,
                    'start_time': (start+timedelta(minutes=i*5)).isoformat(),
                    'end_time': (start+timedelta(minutes=(i+1)*5)).isoformat(), 'synthetic': True,
                    'hr': {'metadata': metadata('hr', 'bpm'), 'mean_hr_bpm': mean,
                           'min_hr_bpm': max(40, round(mean-3, 4)), 'max_hr_bpm': min(180, round(mean+3, 4)),
                           'hr_std_bpm': 1.5, 'hr_change_bpm': None if previous_hr is None else round(mean-previous_hr, 4)},
                    'acc': {'metadata': metadata('activity', 'synthetic_activity_unit'),
                            'activity_magnitude_mean': magnitude, 'activity_magnitude_std': round(magnitude*0.2, 4),
                            'trend': 'unavailable' if previous_acc is None else 'increasing' if magnitude-previous_acc > 0.03 else 'decreasing' if magnitude-previous_acc < -0.03 else 'stable'}})
                truth.append({'window_id': wid, 'ground_truth_state': state})
                previous_hr, previous_acc = mean, magnitude
            subject_days.append({'subject_id': sid, 'day_id': day_id, 'calendar_day': start.date().isoformat(),
                'scenario_id': f'group_{subject_no:03d}_{day_no:03d}', 'schedule': schedule,
                'dynamics': {'ramp_windows': ramp, 'day_hr_offset': day_offset, 'intensity': intensity},
                'sensor_windows': rows, 'ground_truth': truth, 'events': events})
    return {'schema_version': 1, 'seed': seed, 'mode': mode, 'bounds': BOUNDS,
            'profile_ranges': settings['profile_ranges'], 'schedule_ranges': settings['schedule_ranges'], 'generation_settings': settings,
            'profiles': profiles, 'subject_days': subject_days}


def export_multiday(data, output):
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    entries = []
    combined = {k: [] for k in ('sensor_windows', 'ground_truth', 'events')}
    for day in data['subject_days']:
        relative = Path('subjects')/day['subject_id']/day['day_id']
        directory = path/relative
        directory.mkdir(parents=True, exist_ok=True)
        entries.append({k: day[k] for k in ('subject_id', 'day_id', 'calendar_day', 'scenario_id', 'schedule', 'dynamics')} | {'path': str(relative)})
        for key in combined:
            write_json(directory/(key+'.json'), day[key])
            combined[key].extend(day[key])
    for key, values in combined.items():
        write_jsonl(path/('all_'+key+'.jsonl'), values)
    manifest = {k: v for k, v in data.items() if k != 'subject_days'}
    manifest.update(subject_days=entries, subject_count=len(data['profiles']), subject_day_count=len(entries),
                    window_count=len(combined['sensor_windows']), clean=True,
                    state_counts=dict(Counter(t['ground_truth_state'] for t in combined['ground_truth'])))
    write_json(path/'dataset_manifest.json', manifest)


def load_multiday(path):
    path = Path(path)
    manifest = json.loads((path/'dataset_manifest.json').read_text())
    days = []
    for entry in manifest['subject_days']:
        directory = (path/entry['path']).resolve()
        if not directory.is_relative_to(path.resolve()):
            raise ValueError('manifestのパスがデータセット外です')
        days.append({**entry, **{k: json.loads((directory/(k+'.json')).read_text())
                                for k in ('sensor_windows', 'ground_truth', 'events')}})
    return {**manifest, 'subject_days': days}


def main():
    parser = argparse.ArgumentParser(description='複数subject・複数日の合成fixtureを生成します（医学的実データではありません）')
    parser.add_argument('--output', type=Path, required=True, help='Git外の出力先')
    parser.add_argument('--config', type=Path, help='任意の生成範囲JSON設定（PHASE5E.md参照）')
    parser.add_argument('--subjects', type=int, default=4, help='合成subject数')
    parser.add_argument('--days', type=int, default=7, help='subjectあたり日数')
    parser.add_argument('--seed', type=int, default=42, help='決定的乱数seed')
    parser.add_argument('--mode', choices=('simple', 'overlapping'), default='overlapping', help='分布の難易度')
    args = parser.parse_args()
    try:
        data = generate_multiday(args.subjects, args.days, args.seed, args.mode,
            json.loads(args.config.read_text()) if args.config else None)
        export_multiday(data, args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'{args.subjects} subject × {args.days}日、{args.subjects*args.days*288}窓を生成しました: {args.output}')

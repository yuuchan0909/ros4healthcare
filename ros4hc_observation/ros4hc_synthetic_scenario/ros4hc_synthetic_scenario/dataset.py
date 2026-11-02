"""独立した合成データ生成・検証・出力。実センサ測定ではない。"""
import copy
import csv
import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

LABELS = dict(zip(
    ['low_activity', 'light_activity', 'walking_like_activity', 'high_activity',
     'post_activity_recovery', 'indeterminate'],
    ['低活動状態', '軽活動状態', '歩行様活動', '高活動状態', '活動後回復状態', '判断不能']))
PERTURBATIONS = ('missing_hr', 'missing_acc', 'missing_both', 'unknown_unit_metadata',
                 'hr_anomaly', 'acc_anomaly', 'sensor_contradiction')


def load_config(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_config(c):
    def require(ok):
        if not ok:
            raise ValueError('設定値またはスケジュールが不正です')
    require(c['duration_minutes'] == 1440 and c['window_minutes'] == 5)
    require(isinstance(c['subject_id'], str) and bool(c['subject_id']))
    start = datetime.fromisoformat(c['start_time'])
    require(start.utcoffset() is not None)
    pos = 0
    for s in c['schedule']:
        require(s['start_minute'] == pos and s['end_minute'] > pos)
        require(s['end_minute'] % 5 == 0 and s['state'] in LABELS)
        require(s['state'] != 'indeterminate')
        pos = s['end_minute']
    require(pos == 1440)
    for key in ('hr', 'activity'):
        v = c[key]
        nums = v['bounds'] + list(v['baselines'].values()) + [v['jitter']]
        require(all(isinstance(x, (int, float)) and math.isfinite(x) for x in nums))
        lo, hi = v['bounds']
        require(0 <= lo < hi and v['jitter'] >= 0)
        require(all(lo <= x <= hi for x in v['baselines'].values()))
        required = set(LABELS) - {'indeterminate', 'post_activity_recovery'}
        if key == 'activity':
            required.add('post_activity_recovery')
        require(required <= v['baselines'].keys())
    h = c['hr']
    require(all(math.isfinite(h[k]) for k in ('spread', 'recovery_start', 'recovery_step')))
    require(h['spread'] >= 0 and h['recovery_step'] > 0)
    require(math.isfinite(c['activity']['std_ratio']) and 0 <= c['activity']['std_ratio'] <= 1)
    for s in c['schedule']:
        if s['state'] == 'post_activity_recovery':
            n = (s['end_minute'] - s['start_minute']) // 5
            require(h['bounds'][0] <= h['recovery_start'] - (n-1)*h['recovery_step'])
            require(h['recovery_start'] <= h['bounds'][1])


def metadata(kind, unit):
    return {'observation_type': kind, 'unit': unit, 'unit_status': 'UNIT_STATUS_VERIFIED',
            'data_level': 'DATA_SYNTHETIC_SAMPLE', 'source': 'synthetic_' + kind + '_generator',
            'time_semantics': 'TIME_SYNTHETIC', 'availability': 'available',
            'quality': {'validity': 'VALIDITY_VALID', 'quality_available': False,
                        'quality_value': None, 'quality_scale': ''}}


def generate(c, seed=None):
    validate_config(c)
    rng = random.Random(c['seed'] if seed is None else seed)
    start = datetime.fromisoformat(c['start_time'])
    rows, truth = [], []
    previous_hr = previous_acc = None
    for i in range(288):
        minute = i * 5
        s = next(s for s in c['schedule'] if s['start_minute'] <= minute < s['end_minute'])
        state = s['state']
        h, a = c['hr'], c['activity']
        clamp = lambda x, bounds: round(max(bounds[0], min(bounds[1], x)), 4)
        if state == 'post_activity_recovery':
            mean = h['recovery_start'] - ((minute-s['start_minute'])//5)*h['recovery_step']
        else:
            mean = h['baselines'][state] + rng.uniform(-h['jitter'], h['jitter'])
        mean = clamp(mean, h['bounds'])
        magnitude = clamp(a['baselines'][state] + rng.uniform(-a['jitter'], a['jitter']), a['bounds'])
        hr = {'metadata': metadata('hr', 'bpm'), 'mean_hr_bpm': mean,
              'min_hr_bpm': clamp(mean-h['spread'], h['bounds']),
              'max_hr_bpm': clamp(mean+h['spread'], h['bounds']),
              'hr_change_bpm': None if previous_hr is None else round(mean-previous_hr, 4)}
        acc = {'metadata': metadata('activity', 'synthetic_activity_unit'),
               'activity_magnitude_mean': magnitude,
               'activity_magnitude_std': round(magnitude*a['std_ratio'], 4),
               'trend': 'unavailable' if previous_acc is None else
               ('increasing' if magnitude-previous_acc > a['jitter']*2 else
                'decreasing' if previous_acc-magnitude > a['jitter']*2 else 'stable')}
        wid = f"{c['subject_id']}_{i:03d}"
        rows.append({'window_id': wid, 'subject_id': c['subject_id'], 'index': i,
                     'previous_window_id': rows[-1]['window_id'] if rows else None,
                     'start_time': (start+timedelta(minutes=minute)).isoformat(),
                     'end_time': (start+timedelta(minutes=minute+5)).isoformat(),
                     'synthetic': True, 'hr': hr, 'acc': acc})
        truth.append({'window_id': wid, 'ground_truth_state': state, 'state_label_ja': LABELS[state]})
        previous_hr, previous_acc = mean, magnitude
    result = {'sensor_windows': rows, 'ground_truth': truth}
    validate_dataset(result, c)
    return result


def validate_dataset(d, c):
    def require(ok):
        if not ok:
            raise ValueError('データの連続性・範囲・分離条件が不正です')
    validate_config(c)
    rows, truth = d['sensor_windows'], d['ground_truth']
    if len(rows) != 288 or len(truth) != 288:
        raise ValueError('288窓が必要です')
    ids = set()
    start = datetime.fromisoformat(c['start_time'])
    last_recovery = None
    for i, (r, t) in enumerate(zip(rows, truth)):
        require(r['window_id'] not in ids)
        ids.add(r['window_id'])
        require(r['subject_id'] == c['subject_id'] and r['index'] == i)
        require(r['previous_window_id'] == (rows[i-1]['window_id'] if i else None))
        require(datetime.fromisoformat(r['start_time']) == start + timedelta(minutes=i*5))
        require(datetime.fromisoformat(r['end_time']) == start + timedelta(minutes=(i+1)*5))
        require(t['window_id'] == r['window_id'])
        s = next(s for s in c['schedule'] if s['start_minute'] <= i*5 < s['end_minute'])
        require(t['ground_truth_state'] == s['state'])
        for key, fields in [('hr', ['mean_hr_bpm', 'min_hr_bpm', 'max_hr_bpm']),
                            ('acc', ['activity_magnitude_mean', 'activity_magnitude_std'])]:
            lo, hi = c['hr' if key == 'hr' else 'activity']['bounds']
            for field in fields:
                value = r[key][field]
                require(math.isfinite(value) and (0 if field.endswith('std') else lo) <= value <= hi)
        h = r['hr']
        require(h['min_hr_bpm'] <= h['mean_hr_bpm'] <= h['max_hr_bpm'])
        require(h['hr_change_bpm'] is None if i == 0 else math.isfinite(h['hr_change_bpm']))
        if s['state'] == 'post_activity_recovery':
            if i*5 > s['start_minute']:
                require(abs(last_recovery-h['mean_hr_bpm']-c['hr']['recovery_step']) < 1e-6)
            last_recovery = h['mean_hr_bpm']
    visible = json.dumps(rows, ensure_ascii=False)
    require('ground_truth' not in visible and all(label not in visible for label in LABELS))


def temporal_context(rows, index, previous=0):
    if previous < 0 or not 0 <= index < len(rows):
        raise ValueError('窓位置または履歴数が不正です')
    return copy.deepcopy(rows[max(0, index-previous):index+1])


def perturb(d, kind, index):
    if kind not in PERTURBATIONS or not 0 <= index < len(d['sensor_windows']):
        raise ValueError('摂動種別または窓位置が不正です')
    out = copy.deepcopy(d)
    r = out['sensor_windows'][index]
    for key, missing in [('hr', ('missing_hr', 'missing_both')), ('acc', ('missing_acc', 'missing_both'))]:
        if kind in missing:
            meta = r[key]['metadata']
            meta['availability'] = 'missing'
            meta['quality']['validity'] = 'VALIDITY_MISSING'
            r[key] = {'metadata': meta}
    if kind == 'unknown_unit_metadata':
        for key in ('hr', 'acc'):
            r[key]['metadata'].update(unit='', unit_status='UNIT_STATUS_UNKNOWN')
    if kind in ('hr_anomaly', 'sensor_contradiction'):
        r['hr'].update(mean_hr_bpm=175.0, min_hr_bpm=170.0, max_hr_bpm=180.0)
        r['hr']['hr_change_bpm'] = None
    if kind == 'acc_anomaly':
        r['acc'].update(activity_magnitude_mean=20.0, activity_magnitude_std=10.0, trend='unavailable')
    if kind == 'sensor_contradiction':
        r['acc'].update(activity_magnitude_mean=0.01, activity_magnitude_std=0.002, trend='unavailable')
    # 摂動の指定と期待値はAI入力ではなく評価側にのみ保持する。
    out.setdefault('perturbations', []).append({'window_id': r['window_id'], 'kind': kind,
        'expected_state': 'indeterminate' if kind == 'missing_both' else None})
    return out


def export(d, output):
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    for name, value in [('sensor_windows', d['sensor_windows']), ('ground_truth', d['ground_truth'])]:
        (path/(name+'.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    if 'perturbations' in d:
        (path/'perturbations.json').write_text(json.dumps(d['perturbations'], ensure_ascii=False, indent=2), encoding='utf-8')
    with (path/'daily_windows.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['窓ID', '対象者ID', '開始時刻', '終了時刻', '平均心拍数(bpm)', '最小心拍数(bpm)', '最大心拍数(bpm)', '心拍変化(bpm)', '活動量平均(合成単位)', '活動量標準偏差', '活動量傾向', 'HR利用可否', 'ACC利用可否'])
        for r in d['sensor_windows']:
            h, a = r['hr'], r['acc']
            writer.writerow([r['window_id'], r['subject_id'], r['start_time'], r['end_time'],
                *[h.get(k) for k in ('mean_hr_bpm', 'min_hr_bpm', 'max_hr_bpm', 'hr_change_bpm')],
                *[a.get(k) for k in ('activity_magnitude_mean', 'activity_magnitude_std', 'trend')],
                h['metadata']['availability'], a['metadata']['availability']])
    counts = {s: sum(t['ground_truth_state'] == s for t in d['ground_truth']) for s in LABELS}
    summary = '# 合成24時間シナリオ（評価者専用）\n\n'
    summary += f"対象者: {d['sensor_windows'][0]['subject_id']}。{len(d['sensor_windows'])}窓、各5分。\n\n"
    summary += '研究用fixtureであり、生理学的・臨床的妥当性を主張しません。実センサ測定ではありません。\n\n'
    summary += '\n'.join(f'- {LABELS[s]} ({s}): {n}窓' for s, n in counts.items())
    summary += '\n\nAIには sensor_windows.json または daily_windows.csv のみを渡してください。\n'
    (path/'scenario_summary_ja.md').write_text(summary, encoding='utf-8')

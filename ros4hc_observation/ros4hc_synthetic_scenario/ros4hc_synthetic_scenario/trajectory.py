"""Phase 5D: 観測履歴と評価専用未来ターゲットの分離。"""
import argparse
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from .dataset import LABELS
from .sensor_language import HR_FIELDS, ACC_FIELDS, audit_input, sensor_facts


def audit_model_input(value):
    """許可スキーマと値を検査。未定義フィールドはfail closed。"""
    if set(value) != {'history', 'history_window_count', 'history_minutes', 'current_index',
                      'action', 'event', 'context', 'intervention'}:
        raise ValueError('モデル入力のスキーマが不正です')
    for key in ('action', 'event', 'context', 'intervention'):
        if value[key] != {'known': False, 'value': None}:
            raise ValueError('現fixtureの条件はUNKNOWNである必要があります')
    windows = value['history']
    if not windows or value['history_window_count'] != len(windows) or value['current_index'] != len(windows)-1:
        raise ValueError('履歴長が不正です')
    if value['history_minutes'] != (len(windows)-1)*5:
        raise ValueError('履歴時間が不正です')
    for i, window in enumerate(windows):
        if set(window) != {'relative_minutes', 'hr', 'acc'} or window['relative_minutes'] != (i-len(windows)+1)*5:
            raise ValueError('履歴順序または未来情報が不正です')
        for modality, fields in (('hr', HR_FIELDS), ('acc', ACC_FIELDS)):
            if not set(window[modality]) <= set(fields) | {'metadata'}:
                raise ValueError('観測の未許可フィールドです')
        audit_input(window)


@dataclass(frozen=True)
class TrajectorySample:
    trajectory_id: str
    model_input: dict
    evaluator_target: dict


def build_trajectories(data, history_windows=6, horizons=(5, 15, 30), include_metadata=False,
                       scenario_id=None):
    """subject・暦日ごとに作成。先頭は利用可能な履歴のみ。split情報は評価側のみ。"""
    if type(history_windows) is not int or history_windows < 0:
        raise ValueError('履歴窓数が不正です')
    horizons = tuple(horizons)
    if not horizons or len(set(horizons)) != len(horizons) or any(type(h) is not int or h <= 0 or h % 5 for h in horizons):
        raise ValueError('horizonは重複しない正の5分単位です')
    rows, truth = data['sensor_windows'], data['ground_truth']
    targets = {t['window_id']: t['ground_truth_state'] for t in truth}
    if len(targets) != len(truth) or len(rows) != len(truth) or len({r['window_id'] for r in rows}) != len(rows):
        raise ValueError('窓ID・正解対応が不正です')
    groups = {}
    for row in rows:
        start, end = (datetime.fromisoformat(row[k]) for k in ('start_time', 'end_time'))
        if start.utcoffset() is None or end-start != timedelta(minutes=5) or targets.get(row['window_id']) not in LABELS:
            raise ValueError('5分窓または正解が不正です')
        key = (row['subject_id'], start.date().isoformat())
        group = groups.setdefault(key, [])
        if group and datetime.fromisoformat(group[-1]['end_time']) != start:
            raise ValueError('同subject・同日内の順序または連続性が不正です')
        group.append(row)
    samples = []
    for (subject, day), group in groups.items():
        for i in range(len(group)-max(horizons)//5):
            history = [{'relative_minutes': (j-i)*5, **sensor_facts(group[j], include_metadata)}
                       for j in range(max(0, i-history_windows), i+1)]
            inp = {'history': history, 'history_window_count': len(history),
                   'history_minutes': (len(history)-1)*5, 'current_index': len(history)-1,
                   **{k: {'known': False, 'value': None} for k in ('action', 'event', 'context', 'intervention')}}
            audit_model_input(inp)
            sid = hashlib.sha256(json.dumps([subject, day, group[i]['start_time'], scenario_id], ensure_ascii=False).encode()).hexdigest()
            current_state = targets[group[i]['window_id']]
            future = []
            for h in horizons:
                row = group[i+h//5]
                state = targets[row['window_id']]
                future.append({'horizon_minutes': h, 'mean_hr_bpm': row['hr'].get('mean_hr_bpm'),
                               'activity_magnitude_mean': row['acc'].get('activity_magnitude_mean'),
                               'activity_magnitude_std': row['acc'].get('activity_magnitude_std'),
                               'future_state': state,
                               'transition_category': 'same-state' if state == current_state else 'transition'})
            target = {'subject_id': subject, 'day_id': day, 'scenario_id': scenario_id,
                      'source_current_index': group[i]['index'], 'current_start_time': group[i]['start_time'],
                      'current_state': current_state, 'future_targets': future}
            samples.append(TrajectorySample(sid, inp, target))
    return samples


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in rows), encoding='utf-8')


def export_trajectories(samples, output, history_windows=6, horizons=(5, 15, 30)):
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    for s in samples:
        audit_model_input(s.model_input)
    write_jsonl(path/'trajectory_inputs.jsonl', [{'trajectory_id': s.trajectory_id, 'model_input': s.model_input} for s in samples])
    write_jsonl(path/'trajectory_targets.jsonl', [{'trajectory_id': s.trajectory_id, **s.evaluator_target} for s in samples])
    write_json(path/'trajectory_manifest.json', {'schema_version': 1, 'sample_count': len(samples),
        'previous_history_windows': history_windows, 'horizons_minutes': list(horizons),
        'split_group_fields': ['subject_id', 'day_id', 'scenario_id'],
        'split_metadata_location': 'trajectory_targets.jsonl',
        'random_window_split_recommended': False})
    (path/'trajectory_summary_ja.md').write_text(f'# 軌跡データ\n\n{len(samples)}件。過去最大{history_windows}窓＋現在窓。予測先: {list(horizons)}分。\n\nモデルにはmodel_inputだけを渡します。条件4項目はUNKNOWNです。\n重複履歴のランダム窓分割は時間漏洩を生みます。subject/day/scenarioで分割し、境界を跨ぐ履歴・ターゲットを排除してください。1日では汎化性能を主張できません。\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='モデル非依存の未来予測用軌跡を生成します')
    parser.add_argument('--scenario-dir', type=Path, required=True, help='Phase 5A出力先')
    parser.add_argument('--output', type=Path, required=True, help='生成先（Git外を指定）')
    parser.add_argument('--history-windows', type=int, default=6, help='現在窓を除く過去窓数')
    parser.add_argument('--horizons', default='5,15,30', help='予測先の分（カンマ区切り）')
    parser.add_argument('--include-metadata', action='store_true', help='観測メタデータを保持')
    args = parser.parse_args()
    try:
        data = {k: json.loads((args.scenario_dir/(k+'.json')).read_text()) for k in ('sensor_windows', 'ground_truth')}
        horizons = tuple(int(x) for x in args.horizons.split(','))
        samples = build_trajectories(data, args.history_windows, horizons, args.include_metadata)
        export_trajectories(samples, args.output, args.history_windows, horizons)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(f'軌跡{len(samples)}件を生成しました: {args.output}。1日fixtureの学習・汎化性能は主張できません。')

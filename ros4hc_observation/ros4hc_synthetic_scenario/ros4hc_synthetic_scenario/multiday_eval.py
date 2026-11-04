"""Phase 5E: group分割・評価者専用診断・既存baselineの適用。"""
import argparse
from collections import Counter
import itertools
import json
import math
from pathlib import Path

from .dataset import LABELS
from .multiday import load_multiday
from .trajectory import build_trajectories, export_trajectories, write_json
from .world_model_eval import evaluate, scores_markdown
from .world_model_interface import PersistenceBaseline


def multiday_scores_markdown(scores):
    return scores_markdown(scores).replace('1日synthetic fixtureのみです。',
        '複数日の制御されたsynthetic fixtureです。baselineは学習せず、split別の値はデータ特性です。')


def group_key(day):
    return (day['subject_id'], day['calendar_day'])


def assign_splits(days, strategy='research', validation_groups=(), test_groups=()):
    """research / leave-subject-out / leave-day-out / scenario-day-group。

    指定groupは評価者側。subject/day/scenarioをグループごと完全に留保する。
    """
    if strategy not in ('research', 'leave-subject-out', 'leave-day-out', 'scenario-day-group'):
        raise ValueError('未知の分割方式です')
    if set(validation_groups) & set(test_groups):
        raise ValueError('validationとtestの指定groupが重複しています')
    subjects = sorted({d['subject_id'] for d in days})
    dates = sorted({d['calendar_day'] for d in days})
    mapping = {}
    for day in days:
        key = group_key(day)
        if key in mapping:
            raise ValueError('subject-dayが重複しています')
        if strategy == 'research':
            split = 'test' if day['subject_id'] == subjects[-1] else 'validation' if day['calendar_day'] in dates[-2:] else 'train'
        else:
            field = {'leave-subject-out': 'subject_id', 'leave-day-out': 'calendar_day',
                     'scenario-day-group': 'scenario_id'}[strategy]
            value = day[field]
            split = 'test' if value in test_groups else 'validation' if value in validation_groups else 'train'
        mapping[key] = split
    if strategy != 'research':
        field = {'leave-subject-out': 'subject_id', 'leave-day-out': 'calendar_day', 'scenario-day-group': 'scenario_id'}[strategy]
        available = {d[field] for d in days}
        if not (set(validation_groups) | set(test_groups)) <= available:
            raise ValueError('存在しない分割groupです')
    return mapping


def validate_split_mapping(days, mapping):
    keys = [group_key(d) for d in days]
    if len(set(keys)) != len(keys) or set(keys) != set(mapping) or any(s not in ('train', 'validation', 'test') for s in mapping.values()):
        raise ValueError('分割の重複・欠落・未定義ラベルです')


def trajectories_for_days(days, history=6, horizons=(5, 15, 30)):
    samples = []
    seen = set()
    for day in days:
        rows, truth = day['sensor_windows'], day['ground_truth']
        if len(rows) != 288 or len(truth) != 288 or group_key(day) in seen:
            raise ValueError('subject-day重複または288窓でない日です')
        seen.add(group_key(day))
        if any(r['subject_id'] != day['subject_id'] or r['index'] != i or r['start_time'][:10] != day['calendar_day'] for i, r in enumerate(rows)):
            raise ValueError('日・subject・窓位置が不正です')
        samples.extend(build_trajectories(day, history, horizons, scenario_id=day['scenario_id']))
    if len({s.trajectory_id for s in samples}) != len(samples):
        raise ValueError('trajectory ID重複です')
    return samples


def describe(values):
    if not values:
        return {'count': 0, 'mean': None, 'std': None, 'min': None, 'max': None}
    mean = sum(values)/len(values)
    return {'count': len(values), 'mean': mean,
            'std': math.sqrt(sum((x-mean)**2 for x in values)/len(values)),
            'min': min(values), 'max': max(values)}


def diagnostics(days, samples):
    state_counts = Counter({s: 0 for s in LABELS})
    events = Counter()
    values = {s: {'hr': [], 'acc': []} for s in LABELS}
    adjacent = Counter()
    subject_values = {}
    for day in days:
        truth = day['ground_truth']
        subject = subject_values.setdefault(day['subject_id'], {'hr': [], 'acc': []})
        for i, (r, t) in enumerate(zip(day['sensor_windows'], truth)):
            state = t['ground_truth_state']
            state_counts[state] += 1
            subject['hr'].append(r['hr']['mean_hr_bpm'])
            subject['acc'].append(r['acc']['activity_magnitude_mean'])
            values[state]['hr'].append(r['hr']['mean_hr_bpm'])
            values[state]['acc'].append(r['acc']['activity_magnitude_mean'])
            if i and truth[i-1]['ground_truth_state'] != state:
                adjacent[truth[i-1]['ground_truth_state']+' -> '+state] += 1
        events.update(e['event_type'] for e in day['events'])
    distributions = {s: {f: describe(v) for f, v in fields.items()} for s, fields in values.items()}
    overlap = []
    for a, b in itertools.combinations(LABELS, 2):
        for field in ('hr', 'acc'):
            x, y = distributions[a][field], distributions[b][field]
            if x['count'] and y['count']:
                overlap.append({'states': [a, b], 'field': field,
                    'range_overlap_width': max(0, min(x['max'], y['max'])-max(x['min'], y['min']))})
    matrices, transitions = {}, {}
    for sample in samples:
        current = sample.evaluator_target['current_state']
        for target in sample.evaluator_target['future_targets']:
            h = str(target['horizon_minutes'])
            matrix = matrices.setdefault(h, {s: {t: 0 for t in LABELS} for s in LABELS})
            matrix[current][target['future_state']] += 1
            transitions.setdefault(h, Counter())[target['transition_category']] += 1
    return {'subject_day_count': len(days), 'windows': sum(state_counts.values()), 'trajectories': len(samples),
            'state_counts': dict(state_counts), 'event_counts': dict(events),
            'adjacent_transition_types': dict(adjacent), 'adjacent_transition_count': sum(adjacent.values()),
            'transition_counts': {h: dict(c) for h, c in transitions.items()}, 'transition_matrices': matrices,
            'state_distributions': distributions, 'range_overlaps': overlap,
            'subject_distributions': {sid: {f: describe(v) for f, v in fields.items()} for sid, fields in subject_values.items()}}


def export_research(data, output, history=6, horizons=(5, 15, 30), strategy='research',
                    validation_groups=(), test_groups=()):
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    days = data['subject_days']
    mapping = assign_splits(days, strategy, validation_groups, test_groups)
    validate_split_mapping(days, mapping)
    samples = trajectories_for_days(days, history, horizons)
    export_trajectories(samples, path, history, horizons)
    # 5Dの単日向け説明を、複数日fixtureの説明に置換する。
    (path/'trajectory_summary_ja.md').write_text(
        f'# 複数日軌跡\n\n{len(samples)}件。過去{history}窓＋現在、horizon={horizons}分。日境界を跨ぎません。\n'
        'モデルにはmodel_inputのみを渡します。条件はUNKNOWNです。分割・診断・イベントは評価者専用です。\n'
        'ランダム窓分割は時間漏洩を生みます。持続予測は未学習のデータ特性評価でありWorld Modelの性能ではありません。\n', encoding='utf-8')
    report = {'schema_version': 1, 'strategy': strategy, 'random_window_split_recommended': False, 'splits': {}}
    all_scores = {}
    for split in ('overall', 'train', 'validation', 'test'):
        selected_days = days if split == 'overall' else [d for d in days if mapping[group_key(d)] == split]
        keys = {group_key(d) for d in selected_days}
        selected = [s for s in samples if (s.evaluator_target['subject_id'], s.evaluator_target['day_id']) in keys]
        diag = diagnostics(selected_days, selected)
        _, scores = evaluate(selected, PersistenceBaseline())
        all_scores[split] = scores
        if split != 'overall':
            report['splits'][split] = {'subject_days': [{k: d[k] for k in ('subject_id', 'day_id', 'calendar_day', 'scenario_id')} for d in selected_days], **diag}
        else:
            write_json(path/'dataset_diagnostics.json', diag)
        (path/f'persistence_{split}_scores_ja.md').write_text(multiday_scores_markdown(scores), encoding='utf-8')
    write_json(path/'split_manifest.json', report)
    write_json(path/'persistence_split_scores.json', all_scores)
    examples = {}
    for kind in ('transition', 'same-state'):
        s = next((s for s in samples if all(t['transition_category'] == kind for t in s.evaluator_target['future_targets'])), None)
        if s is not None:
            examples[kind] = s
    write_json(path/'representative_model_inputs.json', {k: s.model_input for k, s in examples.items()})
    write_json(path/'representative_evaluator_targets.json', {k: s.evaluator_target for k, s in examples.items()})
    summary = '# Phase 5E データ特性（評価者専用）\n\n'
    summary += f"{len(data['profiles'])} subject、{len(days)} subject-days、{len(samples)}軌跡。mode={data['mode']}。\n"
    summary += '生理学的・臨床的妥当性はありません。持続予測baselineは学習せず、World Modelの性能ではありません。\n\n'
    overall_diag = diagnostics(days, samples)
    summary += '\n## 状態窓数と隣接遷移・イベント（評価者専用）\n\n'
    summary += '|状態|全体|train|validation|test|\n|---|---:|---:|---:|---:|\n'
    for state in LABELS:
        summary += '|'+state+'|'+str(overall_diag['state_counts'][state])+'|'+ '|'.join(str(report['splits'][split]['state_counts'][state]) for split in ('train','validation','test'))+'|\n'
    summary += '\n隣接状態遷移: '+str(overall_diag['adjacent_transition_count'])+'件。\n\n'
    summary += '```json\n'+json.dumps({'transition_types': overall_diag['adjacent_transition_types'], 'events': overall_diag['event_counts'], 'horizon_transitions': overall_diag['transition_counts']}, ensure_ascii=False, indent=2)+'\n```\n'
    summary += '\n## 状態別の数値分布（母標準偏差）\n\n|状態|指標|平均|標準偏差|最小|最大|\n|---|---|---:|---:|---:|---:|\n'
    for state, fields in overall_diag['state_distributions'].items():
        for field, stats in fields.items():
            if stats['count']:
                summary += f'|{state}|{field}|'+ '|'.join(f'{stats[k]:.6f}' for k in ('mean','std','min','max'))+'|\n'
    summary += '\n範囲重複幅・subject別分布・6×6遷移行列はdataset_diagnostics.jsonに保存します。重複幅は分布確率の重なりではありません。\n\n'
    summary += '## subjectプロファイル例\n\n```json\n'+json.dumps(data['profiles'][0], indent=2)+'\n```\n'
    summary += '## 同一subjectの異なる日程（評価者専用）\n\n'
    for d in days[:2]:
        summary += f"{d['subject_id']} / {d['day_id']}\n\n```json\n"+json.dumps(d['schedule'], indent=2)+'\n```\n'
    summary += '\nモデル入力例と評価ターゲット例はrepresentative_model_inputs.json / representative_evaluator_targets.jsonに分離しています。\n'
    for split, scores in all_scores.items():
        summary += f'\n## {split}\n\n'+multiday_scores_markdown(scores)
    (path/'dataset_summary_ja.md').write_text(summary, encoding='utf-8')
    return samples, report, all_scores


def main():
    parser = argparse.ArgumentParser(description='複数日軌跡・漏洩を防ぐgroup分割・持続予測評価を生成します')
    parser.add_argument('--dataset-dir', type=Path, required=True, help='複数日データセット')
    parser.add_argument('--output', type=Path, required=True, help='Git外の出力先')
    parser.add_argument('--history-windows', type=int, default=6, help='過去窓数')
    parser.add_argument('--horizons', default='5,15,30', help='予測先（分）')
    parser.add_argument('--split-strategy', choices=('research', 'leave-subject-out', 'leave-day-out', 'scenario-day-group'), default='research', help='subject/day/scenario単位の分割')
    parser.add_argument('--validation-groups', default='', help='留保するgroup ID（カンマ区切り）')
    parser.add_argument('--test-groups', default='', help='test group ID（カンマ区切り）')
    args = parser.parse_args()
    try:
        samples, report, _ = export_research(load_multiday(args.dataset_dir), args.output, args.history_windows,
            tuple(int(v) for v in args.horizons.split(',')), args.split_strategy,
            tuple(filter(None, args.validation_groups.split(','))), tuple(filter(None, args.test_groups.split(','))))
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as exc:
        parser.error(str(exc))
    print(f'{len(samples)}軌跡を生成・評価しました。分割はsubject-day単位、baselineは未学習です: {args.output}')

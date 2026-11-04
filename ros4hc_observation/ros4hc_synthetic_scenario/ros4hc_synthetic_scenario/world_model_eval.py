"""未来観測予測の評価。欠落出力は明示的に未採点とする。"""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
from .dataset import LABELS
from .trajectory import TrajectorySample, audit_model_input, write_json, write_jsonl
from .world_model_interface import PersistenceBaseline, Prediction

FIELDS = ('mean_hr_bpm', 'activity_magnitude_mean')


def evaluate(samples, adapter):
    buckets, predictions = {}, []
    ids = set()
    for sample in samples:
        if sample.trajectory_id in ids:
            raise ValueError('軌跡IDが重複しています')
        ids.add(sample.trajectory_id)
        audit_model_input(sample.model_input)
        targets = sample.evaluator_target['future_targets']
        horizons = tuple(t['horizon_minutes'] for t in targets)
        # 評価正解・IDはadapterへ渡さない。
        import copy
        prediction = adapter.predict(copy.deepcopy(sample.model_input), horizons)
        if not isinstance(prediction, Prediction):
            raise ValueError('adapterはPredictionを返す必要があります')
        for output in (prediction.predicted_observations, prediction.predicted_state):
            if output is not None and (not isinstance(output, dict) or not set(output) <= set(horizons)):
                raise ValueError('予測horizonが不正です')
        predictions.append({'trajectory_id': sample.trajectory_id, **asdict(prediction)})
        for target in targets:
            h = target['horizon_minutes']
            if target['transition_category'] not in ('same-state', 'transition'):
                raise ValueError('遷移区分が不正です')
            obs = (prediction.predicted_observations or {}).get(h, {})
            if not isinstance(obs, dict) or not set(obs) <= set(FIELDS) | {'activity_magnitude_std'}:
                raise ValueError('予測観測が不正です')
            state = (prediction.predicted_state or {}).get(h)
            if state is not None and state not in LABELS:
                raise ValueError('予測状態が不正です')
            for group in ('all', target['transition_category']):
                b = buckets.setdefault(h, {}).setdefault(group, {'sample_count': 0, 'errors': {f: [] for f in FIELDS}, 'pairs': []})
                b['sample_count'] += 1
                for field in FIELDS:
                    p, t = obs.get(field), target.get(field)
                    for v in (p, t):
                        if v is not None and (type(v) not in (int, float) or not math.isfinite(v)):
                            raise ValueError('数値予測または正解が非有限・非数値です')
                    if p is not None and t is not None:
                        b['errors'][field].append(p-t)
                if state is not None:
                    b['pairs'].append((target['future_state'], state))
    scores = {}
    for h, groups in sorted(buckets.items()):
        scores[str(h)] = {}
        for group in ('all', 'same-state', 'transition'):
            b = groups.get(group, {'sample_count': 0, 'errors': {f: [] for f in FIELDS}, 'pairs': []})
            result = {'sample_count': b['sample_count']}
            for field, errors in b['errors'].items():
                n = len(errors)
                result[field] = {'count': n, 'missing_count': b['sample_count']-n,
                    'mae': sum(abs(e) for e in errors)/n if n else None,
                    'rmse': math.sqrt(sum(e*e for e in errors)/n) if n else None}
            pairs = b['pairs']
            classification = {'count': len(pairs), 'missing_count': b['sample_count']-len(pairs),
                              'accuracy': sum(t == p for t, p in pairs)/len(pairs) if pairs else None}
            present = {t for t, _ in pairs}
            f1 = []
            for label in present:
                tp = sum(t == p == label for t, p in pairs)
                fp = sum(p == label and t != label for t, p in pairs)
                fn = sum(t == label and p != label for t, p in pairs)
                f1.append(2*tp/(2*tp+fp+fn))
            classification['macro_f1_present_classes'] = sum(f1)/len(f1) if f1 else None
            result['state_classification'] = classification
            scores[str(h)][group] = result
    return predictions, scores


def scores_markdown(scores):
    text = '# 持続予測baseline評価\n\nWorld Modelの性能ではありません。ACCは合成単位です。状態予測なしは未採点です。\n\n'
    text += '|先（分）|区分|件数|HR MAE|HR RMSE|ACC MAE|ACC RMSE|\n|---|---|---:|---:|---:|---:|---:|\n'
    for h, groups in scores.items():
        for group, s in groups.items():
            vals = [s[f][m] for f in FIELDS for m in ('mae', 'rmse')]
            text += f'|{h}|{group}|{s["sample_count"]}|'+'|'.join('未採点' if v is None else f'{v:.6f}' for v in vals)+'|\n'
    return text+'\n1日synthetic fixtureのみです。重複軌跡のランダム窓分割には時間漏洩があります。\n'


def main():
    parser = argparse.ArgumentParser(description='持続予測baselineを採点します（World Modelではありません）')
    parser.add_argument('--trajectory-dir', type=Path, required=True, help='軌跡入力・正解のディレクトリ')
    parser.add_argument('--output', type=Path, required=True, help='採点出力先')
    args = parser.parse_args()
    try:
        def read(name):
            return [json.loads(line) for line in (args.trajectory_dir/name).read_text().splitlines()]
        inputs, targets = read('trajectory_inputs.jsonl'), read('trajectory_targets.jsonl')
        by_id = {r['trajectory_id']: r for r in targets}
        if len(by_id) != len(targets) or {r['trajectory_id'] for r in inputs} != set(by_id):
            raise ValueError('入力と正解のID対応が不正です')
        samples = [TrajectorySample(r['trajectory_id'], r['model_input'], by_id[r['trajectory_id']]) for r in inputs]
        predictions, scores = evaluate(samples, PersistenceBaseline())
        args.output.mkdir(parents=True, exist_ok=True)
        write_jsonl(args.output/'persistence_predictions.jsonl', predictions)
        write_json(args.output/'persistence_scores.json', scores)
        (args.output/'persistence_scores_ja.md').write_text(scores_markdown(scores), encoding='utf-8')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(f'持続予測baseline {len(samples)}件を評価しました: {args.output}')

"""Phase 5B: 評価情報から分離した入力条件と決定的な事実記述。"""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from .dataset import LABELS, PERTURBATIONS, perturb

HR_FIELDS = ('mean_hr_bpm', 'min_hr_bpm', 'max_hr_bpm', 'hr_change_bpm', 'hr_std_bpm')
ACC_FIELDS = ('activity_magnitude_mean', 'activity_magnitude_std', 'trend')
META_FIELDS = ('observation_type', 'unit', 'unit_status', 'data_level', 'availability', 'quality', 'source')
CONDITIONS = ('a', 'b', 'c', 'd')
ANSWER_CHOICES = '回答候補（英語ID: 日本語表示）:\n' + '\n'.join(
    f'{state}: {display}' for state, display in LABELS.items()) + '\n'
CLASSIFICATION_PROMPT = ANSWER_CHOICES + '''提供された観測事実のみを使い、上記の回答候補からちょうど1つ選んでください。
根拠のない生活行動（食事・仕事など）を推測せず、疾病を診断しないでください。
情報が不十分な場合は判断不能を選んでください。文献引用は行わないでください。
出力は predicted_state のみを持つJSONオブジェクトとし、値は上記の英語識別子1つにしてください。
観測事実:
{task_input}
'''
EXPLANATION_PROMPT = ANSWER_CHOICES + '''提供された観測事実のみを使い、最も適合する状態を上記の回答候補から選んでください。
支持する具体的センサ事実、結論できないこと、情報不足の有無を説明してください。
根拠のない生活行動を推測せず、疾病を診断しないでください。情報不足なら判断不能を選んでください。
文献引用は禁止します。JSONのみを返してください。
predicted_state は上記の英語識別子1つ、evidence は根拠の文字列配列、
uncertainty は不確実性の文字列、unsupported_inferences_avoided は避けた推測の文字列配列です。
観測事実:
{task_input}
'''


def output_contract(task):
    properties = {'predicted_state': {
        'type': 'string', 'enum': list(LABELS),
        'oneOf': [{'const': state, 'title': f'{letter}. {ja}'}
                  for letter, (state, ja) in zip('ABCDEF', LABELS.items())]}}
    if task == 'explanation':
        properties.update(evidence={'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'pattern': r'\S'}, 'minItems': 1},
                          uncertainty={'type': 'string', 'minLength': 1, 'pattern': r'\S'},
                          unsupported_inferences_avoided={'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'pattern': r'\S'}})
    elif task != 'classification':
        raise ValueError('未知のタスクです')
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def load_scenario(directory):
    path = Path(directory)
    data = {key: json.loads((path/(key+'.json')).read_text(encoding='utf-8'))
            for key in ('sensor_windows', 'ground_truth')}
    extra = path/'perturbations.json'
    if extra.exists():
        data['perturbations'] = json.loads(extra.read_text(encoding='utf-8'))
    rows, truth = data['sensor_windows'], data['ground_truth']
    if len(rows) != 288 or len(truth) != 288:
        raise ValueError('Phase 5Aの288窓が必要です')
    from datetime import datetime, timedelta
    ids = set()
    for i, (row, target) in enumerate(zip(rows, truth)):
        if (row['window_id'] in ids or target['window_id'] != row['window_id'] or
                target['ground_truth_state'] not in LABELS or row['index'] != i):
            raise ValueError('窓ID・順序・正解対応が不正です')
        ids.add(row['window_id'])
        start, end = (datetime.fromisoformat(row[k]) for k in ('start_time', 'end_time'))
        if (start.utcoffset() is None or end-start != timedelta(minutes=5) or
                (i and row['start_time'] != rows[i-1]['end_time'])):
            raise ValueError('5分窓の時刻連続性が不正です')
    return data


def sensor_facts(row, metadata):
    out = {}
    for key, fields in (('hr', HR_FIELDS), ('acc', ACC_FIELDS)):
        source = row[key]
        out[key] = {k: copy.deepcopy(source[k]) for k in fields if k in source}
        if metadata:
            out[key]['metadata'] = {k: copy.deepcopy(source['metadata'][k]) for k in META_FIELDS}
    return out


def make_input(rows, index, condition, history=6):
    if condition not in CONDITIONS or not isinstance(history, int) or history < 0:
        raise ValueError('条件または履歴窓数が不正です')
    if not 0 <= index < len(rows):
        raise ValueError('窓位置が不正です')
    if condition in ('a', 'b'):
        return sensor_facts(rows[index], condition == 'b')
    first = max(0, index-history)
    windows = []
    for position in range(first, index+1):
        window = {'relative_minutes': (position-index)*5,
                  **sensor_facts(rows[position], True)}
        if condition == 'd':
            window.update({k: rows[position][k] for k in ('start_time', 'end_time')})
        windows.append(window)
    return {'window_minutes': 5, 'available_history_windows': index-first,
            'available_history_minutes': (index-first)*5, 'windows': windows}


def _metadata_ja(meta):
    # 値を省略しない。英語enumは機械識別子として併記する。
    names = {'observation_type': '観測型', 'unit': '単位', 'unit_status': '単位の確度',
             'data_level': 'データ水準', 'availability': '利用可否', 'source': '合成生成元',
             'quality': '品質メタデータ'}
    parts = [f'{names[k]}は{json.dumps(v, ensure_ascii=False, sort_keys=True)}である。'
             for k, v in meta.items()]
    if meta['unit_status'] == 'UNIT_STATUS_UNKNOWN':
        parts.append('単位の確度は不明である。')
    return ''.join(parts)


def _window_ja(window):
    parts = []
    if 'relative_minutes' in window:
        offset = window['relative_minutes']
        parts.append('現在の窓。' if offset == 0 else f'{-offset}分前の窓。')
    if 'start_time' in window:
        parts.append(f"開始時刻は{window['start_time']}、終了時刻は{window['end_time']}である。")
    descriptions = {
        'mean_hr_bpm': '心拍数の平均', 'min_hr_bpm': '心拍数の最小値',
        'max_hr_bpm': '心拍数の最大値', 'hr_std_bpm': '心拍数の標準偏差',
        'activity_magnitude_mean': '活動量指標の平均',
        'activity_magnitude_std': '活動量指標の標準偏差'}
    for modality in ('hr', 'acc'):
        values = window[modality]
        fields = HR_FIELDS if modality == 'hr' else ACC_FIELDS
        if not any(k in values for k in fields):
            parts.append('心拍データは利用できない。' if modality == 'hr' else '活動量データは利用できない。')
        for key in fields:
            if key not in values:
                continue
            value = values[key]
            if key == 'trend':
                parts.append({'increasing': '活動量指標は前窓より増加している。',
                              'decreasing': '活動量指標は前窓より減少している。',
                              'stable': '活動量指標の前窓との差は閾値以内である。',
                              'unavailable': '活動量指標の前窓との比較は利用できない。'}[value])
            elif key == 'hr_change_bpm':
                parts.append('平均心拍数の前窓との差は利用できない。' if value is None else
                             f'平均心拍数の前窓との差は{value} bpmである。')
            else:
                unit = ' bpm' if modality == 'hr' else ''
                parts.append(f'{descriptions[key]}は{value}{unit}である。')
        if 'metadata' in values:
            parts.append(_metadata_ja(values['metadata']))
    return ''.join(parts)


def format_ja(task_input):
    if 'windows' not in task_input:
        return _window_ja(task_input)
    return (f"各窓は{task_input['window_minutes']}分である。利用可能な過去文脈は"
            f"{task_input['available_history_windows']}窓、{task_input['available_history_minutes']}分である。\n" +
            '\n'.join(_window_ja(w) for w in task_input['windows']))


# 出力許可キーを固定し、将来のカテゴリ追加による漏洩も検出する。
ALLOWED_KEYS = set(HR_FIELDS+ACC_FIELDS+META_FIELDS) | {
    'hr', 'acc', 'metadata', 'validity', 'quality_available', 'quality_value', 'quality_scale',
    'window_minutes', 'available_history_windows', 'available_history_minutes', 'windows',
    'relative_minutes', 'start_time', 'end_time'}


def audit_input(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in ALLOWED_KEYS:
                raise ValueError(f'AI入力の未許可キー: {key}')
            audit_input(item)
    elif isinstance(value, list):
        for item in value:
            audit_input(item)
    elif isinstance(value, str):
        words = set(re.findall('[a-z]+', value.casefold()))
        if (words & {'state', 'label', 'scenario', 'low', 'medium', 'high', 'light', 'walking',
                     'recovery', 'rest', 'resting', 'sedentary', 'indeterminate'} or
                any(s in value for s in ('低活動', '軽活動', '歩行', '高活動', '回復', '安静',
                                        '休息', '判断不能', '運動中', '状態ラベル'))):
            raise ValueError('AI入力に状態を示す表現があります')
    elif isinstance(value, float):
        import math
        if not math.isfinite(value):
            raise ValueError('AI入力に非有限値があります')


def build_inputs(data, history=6, include_perturbations=False):
    rows = data['sensor_windows']
    truth = data['ground_truth']
    if len(rows) != 288 or len(truth) != 288:
        raise ValueError('288窓と対応する正解が必要です')
    if len({r['window_id'] for r in rows}) != 288 or any(
            row['window_id'] != target['window_id'] or target['ground_truth_state'] not in LABELS
            for row, target in zip(rows, truth)):
        raise ValueError('窓IDと正解の対応が不正です')
    exports = {f'condition_{c}_{representation}': [] for c in CONDITIONS
               for representation in ('structured', 'ja')}
    evaluation = []
    existing = data.get('perturbations', [])
    for variant in (None, *PERTURBATIONS) if include_perturbations else (None,):
        for index, target in enumerate(data['ground_truth']):
            # 各摂動サンプルは当該窓だけを変更する独立したコピー。履歴は元データ。
            candidate = data
            if variant is not None:
                # 摂動APIには当該窓の小さな集合を渡し、日全体の反復deepcopyを避ける。
                changed = perturb({'sensor_windows': [rows[index]], 'ground_truth': [target]}, variant, 0)
                candidate = {'sensor_windows': list(rows),
                             'perturbations': [*existing, *changed['perturbations']]}
                candidate['sensor_windows'][index] = changed['sensor_windows'][0]
            sid = hashlib.sha256(f"phase5b|{target['window_id']}|{variant or 'base'}".encode()).hexdigest()[:24]
            mutations = [m for m in candidate.get('perturbations', [])
                         if m['window_id'] == target['window_id']]
            expected = target['ground_truth_state']
            for mutation in mutations:
                if mutation.get('expected_state') is not None:
                    expected = mutation['expected_state']
            evaluation.append({'sample_id': sid, 'window_id': target['window_id'],
                               'ground_truth_state': expected,
                               'original_ground_truth_state': target['ground_truth_state'],
                               'variant': variant or ('imported_perturbation' if existing else 'clean'),
                               'perturbations': copy.deepcopy(mutations)})
            for condition in CONDITIONS:
                structured = make_input(candidate['sensor_windows'], index, condition, history)
                ja = format_ja(structured)
                audit_input(structured)
                audit_input(ja)
                for representation, content in [('structured', structured), ('ja', ja)]:
                    exports[f'condition_{condition}_{representation}'].append(
                        {'sample_id': sid, 'condition': condition, 'task_input': content})
    return exports, evaluation


def export_inputs(data, output, history=6, include_perturbations=False):
    exports, evaluation = build_inputs(data, history, include_perturbations)
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    for name, records in exports.items():
        (path/(name+'.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n'
                                                for r in records), encoding='utf-8')
    for name, value in [('evaluation_ground_truth', evaluation),
                        ('classification_output_schema', output_contract('classification')),
                        ('explanation_output_schema', output_contract('explanation'))]:
        (path/(name+'.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for task, prompt in [('classification', CLASSIFICATION_PROMPT), ('explanation', EXPLANATION_PROMPT)]:
        (path/(task+'_prompt_ja.txt')).write_text(prompt, encoding='utf-8')
    (path/'dataset_summary_ja.md').write_text(
        '# Phase 5B 入力条件\n\n各条件・各表現: '+str(len(evaluation))+'件。'
        '\n清浄な標準日では288件。摂動を全種類追加すると2304件。\n'
        f'過去文脈の上限: {history}窓。\n\n'
        'A: 前窓との差・傾向を含む現在窓要約。明示的履歴列・メタデータなし。\n'
        'B: Aと同じ要約に自己記述メタデータを追加。\n'
        'C: Bに過去最大30分の明示的な相対履歴列を追加。D: Cに絶対時刻を追加。\n'
        '比較は局所変化特徴を含む単一窓要約と明示的な複数窓文脈の比較です。\n'
        'A/Bにも時間情報があります。時間情報なし対ありの比較ではありません。\n'
        '分類の主要macro-F1は正解support>0のクラス平均macro_f1_present_classesです。\n'
        '全6クラス平均macro_f1_all_classesは診断用です。\n'
        'Condition Dは時刻パターンを利用でき、センサのみの状態推論と解釈してはいけません。\n'
        'AIに渡すのは各レコードのtask_inputだけです。sample_idとconditionは評価側です。\n'
        '正解・摂動はevaluation_ground_truth.jsonに分離しています。LLM呼出しはありません。\n', encoding='utf-8')
    return exports, evaluation


def main():
    parser = argparse.ArgumentParser(description='Phase 5AからSensor-to-Language評価入力を生成します')
    parser.add_argument('--scenario-dir', type=Path, required=True, help='Phase 5A出力ディレクトリ')
    parser.add_argument('--output', type=Path, required=True, help='評価入力の出力先')
    parser.add_argument('--history-windows', type=int, default=6, help='過去窓数（標準6窓＝30分）')
    parser.add_argument('--include-perturbations', action='store_true', help='全7摂動の独立コピーも追加')
    args = parser.parse_args()
    try:
        exports, _ = export_inputs(load_scenario(args.scenario_dir), args.output,
                                  args.history_windows, args.include_perturbations)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(f'入力生成に失敗しました: {exc}')
    print(f'各条件・各表現 {len(exports["condition_a_structured"])}件を生成しました: {args.output}')
    print('Condition Dは時刻を手掛かりにできます。センサのみの推論とは解釈しないでください。')


if __name__ == '__main__':
    main()

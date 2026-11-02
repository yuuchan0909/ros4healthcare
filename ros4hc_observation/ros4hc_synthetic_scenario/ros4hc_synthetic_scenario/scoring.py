"""外部モデル不要の厳密な出力検査と分類採点。"""
import json
from .dataset import LABELS
from .sensor_language import output_contract


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('JSONキーが重複しています')
        obj[key] = value
    return obj


def validate_response(response, task='classification'):
    """JSON文字列またはdictを検証。JSONフェンス・余剰キー・推測修復は許容しない。"""
    contract = output_contract(task)
    try:
        obj = json.loads(response, object_pairs_hook=_unique_object) if isinstance(response, str) else response
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict) or set(obj) != set(contract['required']):
        return False
    if not isinstance(obj['predicted_state'], str) or obj['predicted_state'] not in LABELS:
        return False
    if task == 'explanation':
        if not isinstance(obj['uncertainty'], str) or not obj['uncertainty'].strip():
            return False
        for key in ('evidence', 'unsupported_inferences_avoided'):
            if not isinstance(obj[key], list) or not all(isinstance(v, str) and v.strip() for v in obj[key]):
                return False
        if not obj['evidence']:
            return False
    return True


def score_classification(expected, predictions):
    """sample_id→正解とsample_id→応答を採点。欠落・不正応答も分母に含む。

    未知のsample_idは対応ミスとして拒否する。混同行列は行=正解、列=予測。
    不正応答は行列外で別集計し、各クラスの再現率・F1では偽陰性となる。
    主指標は正解support>0のクラスのみのmacro-F1。全6クラス平均も診断用に返す。
    対象クラスなしの平均と、分母ゼロのクラスF1は0と定義する。
    """
    if not all(isinstance(v, str) and v in LABELS for v in expected.values()):
        raise ValueError('正解ラベルが不正です')
    if set(predictions) - set(expected):
        raise ValueError('正解にないsample_idが予測にあります')
    matrix = {actual: {predicted: 0 for predicted in LABELS} for actual in LABELS}
    support = {state: 0 for state in LABELS}
    invalid_by_class = {state: 0 for state in LABELS}
    correct = invalid = 0
    for sid, actual in expected.items():
        support[actual] += 1
        response = predictions.get(sid)
        if not validate_response(response):
            invalid += 1
            invalid_by_class[actual] += 1
            continue
        obj = json.loads(response) if isinstance(response, str) else response
        predicted = obj['predicted_state']
        matrix[actual][predicted] += 1
        correct += predicted == actual
    per_class = {}
    for state in LABELS:
        tp = matrix[state][state]
        predicted_count = sum(matrix[actual][state] for actual in LABELS)
        precision = tp/predicted_count if predicted_count else 0.0
        recall = tp/support[state] if support[state] else 0.0
        f1 = 2*tp/(support[state]+predicted_count) if support[state]+predicted_count else 0.0
        per_class[state] = {'support': support[state], 'predicted_count': predicted_count,
                            'true_positive': tp, 'invalid_count': invalid_by_class[state],
                            'precision': precision, 'recall': recall, 'f1': f1}
    present = [v['f1'] for v in per_class.values() if v['support'] > 0]
    return {'total_count': len(expected), 'correct_count': correct, 'invalid_output_count': invalid,
            'accuracy': correct/len(expected) if expected else 0.0,
            'per_class': per_class, 'confusion_matrix': matrix,
            'macro_f1_present_classes': sum(present)/len(present) if present else 0.0,
            'macro_f1_all_classes': sum(v['f1'] for v in per_class.values())/len(LABELS)}

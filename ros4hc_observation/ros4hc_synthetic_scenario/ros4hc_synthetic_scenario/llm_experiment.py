"""Phase 5C: 分類実験の構築、再開、応答保存、採点。外部APIには依存しない。"""
import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time
import uuid

from .dataset import LABELS, PERTURBATIONS
from .sensor_language import CLASSIFICATION_PROMPT, audit_input, output_contract
from .scoring import score_classification, validate_response
from .llm_providers import (ExternalProvider, LLMResponse, MockProvider, OpenAIProvider,
                            PermanentProviderError, TransientProviderError)

PROMPT_VERSION = 'phase5c-classification-v1'
PILOT_COUNTS = dict(zip(LABELS, (16, 16, 8, None, None, 8)))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def audit_task_input(task_input):
    audit_input(task_input)
    text = task_input if isinstance(task_input, str) else json.dumps(task_input, ensure_ascii=False)
    if (any(token in text for token in (*PERTURBATIONS, 'ground_truth', 'window_id',
                                        'sample_id', 'subject_id', 'original_ground_truth')) or
            re.search(r'\bindex\b|subject_\d+', text)):
        raise ValueError('観測本文に評価側情報があります')


def build_request(task_input):
    """唯一のモデルrequest構築経路。評価情報を受け取らない。"""
    audit_task_input(task_input)
    content = task_input if isinstance(task_input, str) else json.dumps(task_input, ensure_ascii=False, sort_keys=True)
    return {'prompt': CLASSIFICATION_PROMPT.format(task_input=content),
            'output_schema': output_contract('classification')}


def request_key(provider, model, condition, representation, sample_id, request, decoding):
    return digest({'provider': provider, 'model': model, 'condition': condition,
                   'representation': representation, 'sample_id': sample_id,
                   'task': 'classification', 'prompt_version': PROMPT_VERSION,
                   'request': request, 'decoding': decoding})


def select_samples(evaluation, mode='pilot', seed=42, dataset='clean'):
    if mode not in ('pilot', 'full') or dataset not in ('clean', 'perturbations'):
        raise ValueError('modeまたはdatasetが不正です')
    pool = [row for row in evaluation if (row['variant'] == 'clean') == (dataset == 'clean')]
    if dataset == 'clean' and (len(pool) != 288 or any(row.get('perturbations') for row in pool)):
        raise ValueError('清浄な288窓の評価データが必要です')
    if not pool or len({r['sample_id'] for r in pool}) != len(pool):
        raise ValueError('評価データが空、またはIDが重複しています')
    if any(row['ground_truth_state'] not in LABELS for row in pool):
        raise ValueError('正解ラベルが不正です')
    if mode == 'full':
        return sorted(row['sample_id'] for row in pool)
    selected = []
    for state, limit in PILOT_COUNTS.items():
        group = [row['sample_id'] for row in pool if row['ground_truth_state'] == state]
        group.sort(key=lambda sid: digest([seed, sid]))
        selected.extend(group if limit is None else group[:limit])
    return sorted(selected)


def load_inputs(directory, conditions, representations, dataset, mode, seed):
    path = Path(directory)
    evaluation = json.loads((path/'evaluation_ground_truth.json').read_text(encoding='utf-8'))
    if len({r['sample_id'] for r in evaluation}) != len(evaluation):
        raise ValueError('評価用sample_idが重複しています')
    # 清浄結果に摂動を混ぜない。混合ファイルからも清浄subsetだけを選択する。
    selected = select_samples(evaluation, mode, seed, dataset)
    expected = {r['sample_id']: r['ground_truth_state'] for r in evaluation if r['sample_id'] in selected}
    sources = {'evaluation_ground_truth.json': hashlib.sha256((path/'evaluation_ground_truth.json').read_bytes()).hexdigest()}
    tasks = []
    for condition in conditions:
        for representation in representations:
            filename = f'condition_{condition}_{representation}.jsonl'
            file = path/filename
            sources[filename] = hashlib.sha256(file.read_bytes()).hexdigest()
            records = [json.loads(line) for line in file.read_text(encoding='utf-8').splitlines()]
            by_id = {r['sample_id']: r for r in records}
            if len(by_id) != len(records) or not set(selected) <= set(by_id):
                raise ValueError('入力sample_idの欠落または重複があります')
            for sid in selected:
                row = by_id[sid]
                if set(row) != {'sample_id', 'condition', 'task_input'} or row['condition'] != condition:
                    raise ValueError('Phase 5Bレコード形式が不正です')
                if (representation == 'structured') != isinstance(row['task_input'], dict):
                    raise ValueError('表現形式が不正です')
                request = build_request(row['task_input'])
                # 条件外の絶対時刻やメタデータも拒否する。
                serialized = json.dumps(row['task_input'], ensure_ascii=False)
                if condition != 'd' and ('start_time' in serialized or 'end_time' in serialized or
                                         re.search(r'\d{4}-\d{2}-\d{2}T', serialized)):
                    raise ValueError('条件外の絶対時刻があります')
                if condition == 'a' and any(token in serialized for token in (
                        'metadata', 'unit_status', 'observation_type', 'data_level', 'source',
                        '品質メタデータは', '観測型は', '単位は', '単位の確度', '利用可否は', '合成生成元は')):
                    raise ValueError('Aにメタデータがあります')
                if condition in ('a', 'b') and ('relative_minutes' in serialized or '分前の窓' in serialized):
                    raise ValueError('現在窓条件に履歴列があります')
                tasks.append({'sample_id': sid, 'condition': condition, 'representation': representation,
                              'request': request})
    return selected, expected, sources, tasks


def read_journal(path):
    """末尾の未完行だけを破棄して回復。完結した不正行は拒否する。"""
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        end = raw.rfind(b'\n')+1
        with path.open('r+b') as file:
            file.truncate(end)
        raw = raw[:end]
    return [json.loads(line) for line in raw.splitlines()]


def append(path, value):
    with path.open('a', encoding='utf-8') as file:
        file.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')
        file.flush()
        os.fsync(file.fileno())


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


@contextmanager
def output_lock(path):
    with (path/'.experiment.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('同じ出力先で別の実験が実行中です') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def git_revision():
    root = Path(__file__).resolve().parent
    # installed moduleではcwd（リポジトリルート）を使用する。
    for directory in (root, Path.cwd()):
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    return 'unavailable'


def safe_usage(usage):
    if not isinstance(usage, dict):
        return {}
    return {k: v for k, v in usage.items() if k in ('input_tokens', 'output_tokens', 'total_tokens')
            and type(v) is int and v >= 0}


def import_responses(path, known_keys):
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()]
    output = {}
    for row in rows:
        key = row['request_key']
        if key not in known_keys or key in output or not isinstance(row['raw_response'], str):
            raise ValueError('外部応答のrequest_keyまたはraw_responseが不正です')
        output[key] = LLMResponse(row['raw_response'], safe_usage(row.get('usage', {})))
    return output


def write_scores(path, tasks, expected, responses, manifest):
    latest = {r['request_key']: r for r in responses}
    scores, predictions = {}, {}
    for task in tasks:
        setting = task['condition'].upper()+'-'+task['representation']
        response = latest.get(task['request_key'])
        predictions.setdefault(setting, {})[task['sample_id']] = response['raw_response'] if response else None
    (path/'predictions').mkdir(exist_ok=True)
    (path/'confusion_matrices').mkdir(exist_ok=True)
    for setting, predicted in predictions.items():
        score = score_classification(expected, predicted)
        score['completed_count'] = sum(t['request_key'] in latest for t in tasks
                                       if t['condition'].upper()+'-'+t['representation'] == setting)
        scores[setting] = score
        atomic_json(path/'predictions'/(setting+'.json'), predicted)
        atomic_json(path/'confusion_matrices'/(setting+'.json'), score['confusion_matrix'])
    differences = {}
    for representation in manifest['representations']:
        for left, right in (('A', 'B'), ('B', 'C'), ('C', 'D')):
            if left+'-'+representation in scores and right+'-'+representation in scores:
                a, b = scores[left+'-'+representation], scores[right+'-'+representation]
                differences[right+'-'+left+'-'+representation] = {
                    metric: b[metric]-a[metric] for metric in ('accuracy', 'macro_f1_present_classes')}
    usage = {}
    for row in responses:
        for key, value in safe_usage(row.get('usage', {})).items():
            usage[key] = usage.get(key, 0)+value
    report = {'settings': scores, 'paired_descriptive_differences': differences,
              'usage': usage or None, 'cost': None,
              'response_record_count': len(responses), 'provider_attempt_count': manifest.get('provider_attempt_count', 0),
              'dry_run': manifest['dry_run'], 'provider': manifest['provider']}
    atomic_json(path/'scores.json', report)
    text = '# Phase 5C 分類比較\n\n'
    text += f"provider: {manifest['provider']} / model: {manifest['model']} / mode: {manifest['mode']}\n\n"
    if manifest['dry_run']:
        text += '**dry-run：推論未実施。以下は未取得応答を不正として扱う配線確認値で、モデル性能ではありません。**\n\n'
    if manifest['provider'] == 'mock':
        text += '**mock固定応答による配線確認です。LLMの研究結果ではありません。**\n\n'
    text += '| 条件 | 表現 | Accuracy | Macro-F1（正解ありクラス） | Invalid | 完了/総数 |\n|---|---|---:|---:|---:|---|\n'
    for setting, score in scores.items():
        condition, representation = setting.split('-')
        text += (f"| {condition} | {representation} | {score['accuracy']:.4f} | "
                 f"{score['macro_f1_present_classes']:.4f} | {score['invalid_output_count']} | "
                 f"{score['completed_count']}/{score['total_count']} |\n")
    text += '\n## 対応のある記述的差分\n\n'
    for name, values in differences.items():
        text += f"- {name}: Accuracy差 {values['accuracy']:.4f}、主要Macro-F1差 {values['macro_f1_present_classes']:.4f}\n"
    text += '\n## クラス別結果\n\n'
    for setting, score in scores.items():
        text += f'### {setting}\n\n| 状態 | support | Precision | Recall | F1 |\n|---|---:|---:|---:|---:|\n'
        for state, values in score['per_class'].items():
            text += f"| {LABELS[state]} | {values['support']} | {values['precision']:.4f} | {values['recall']:.4f} | {values['f1']:.4f} |\n"
    text += ('\n費用は取得不可。使用量が未提供の場合は不明です。統計的有意差検定・因果推論は行いません。\n'
             'A/Bにも局所変化特徴があります。Dは時刻の近道を利用でき、センサのみの推論ではありません。\n'
             '固定日程・強いクラス不均衡・高活動/回復各6窓のfixtureであり、一般化の根拠ではありません。\n')
    (path/'scores_ja.md').write_text(text, encoding='utf-8')
    return report


def run_experiment(input_dir, output, provider, mode='pilot', conditions=('a', 'b', 'c', 'd'),
                   representations=('structured', 'ja'), dataset='clean', temperature=0.0,
                   seed=42, provider_seed=None, force=False, dry_run=False, max_attempts=2,
                   retry_delay=1.0, responses_file=None, reasoning_effort=None, max_output_tokens=None):
    if (not conditions or len(set(conditions)) != len(conditions) or not set(conditions) <= set('abcd') or
            not representations or len(set(representations)) != len(representations) or
            not set(representations) <= {'structured', 'ja'} or
            max_attempts < 1 or max_attempts > 3 or retry_delay < 0 or (temperature is not None and not 0 <= temperature <= 2)):
        raise ValueError('実験条件または再試行設定が不正です')
    selected, expected, sources, tasks = load_inputs(input_dir, conditions, representations, dataset, mode, seed)
    decoding = {} if temperature is None else {'temperature': temperature}
    if provider_seed is not None:
        decoding['seed'] = provider_seed
    if reasoning_effort is not None:
        decoding['reasoning_effort'] = reasoning_effort
    if max_output_tokens is not None:
        decoding['max_output_tokens'] = max_output_tokens
    if isinstance(provider, OpenAIProvider):
        # Validate before creating journals or sending any requests, including dry-run.
        provider._payload(provider.model_identifier(), tasks[0]['request'], decoding)
    identity = {'provider': provider.provider_identifier(), 'model': provider.model_identifier()}
    for task in tasks:
        task['request_key'] = request_key(identity['provider'], identity['model'], task['condition'],
                                          task['representation'], task['sample_id'], task['request'], decoding)
    path = Path(output)
    if path.resolve() == Path(input_dir).resolve():
        raise ValueError('出力先は入力先と分離してください')
    path.mkdir(parents=True, exist_ok=True)
    imported = import_responses(responses_file, {t['request_key'] for t in tasks}) if responses_file else {}
    spec = {**identity, 'mode': mode, 'dataset': dataset, 'conditions': list(conditions),
            'representations': list(representations), 'task': 'classification', 'decoding': decoding,
            'sampling_seed': seed, 'selected_sample_ids': selected, 'number_of_samples': len(selected),
            'number_of_requests': len(tasks), 'prompt_template_version': PROMPT_VERSION,
            'prompt_hash': digest([CLASSIFICATION_PROMPT, output_contract('classification')]),
            'dataset_source_identifiers': sources, 'git_commit': git_revision(),
            'implementation_sha256': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                      for name in ('llm_experiment.py', 'llm_providers.py', 'scoring.py', 'sensor_language.py')},
            'max_attempts': max_attempts, 'retry_delay': retry_delay}
    with output_lock(path):
        manifest_path = path/'experiment_manifest.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest['spec_hash'] != digest(spec):
                raise ValueError('既存実験と設定・入力が異なります。別の出力先を指定してください')
        else:
            manifest = {**spec, 'spec_hash': digest(spec), 'experiment_id': uuid.uuid4().hex,
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'expected_states': expected, 'provider_attempt_count': 0,
                        'nondeterminism_warning': 'temperature=0やseed指定でもモデル応答の完全な再現性は保証しません'}
        manifest['dry_run'] = dry_run
        atomic_json(manifest_path, manifest)
        for name in ('requests.jsonl', 'responses.jsonl', 'failures.jsonl'):
            (path/name).touch(exist_ok=True)
        logged = {r['request_key'] for r in read_journal(path/'requests.jsonl')}
        responses = read_journal(path/'responses.jsonl')
        read_journal(path/'failures.jsonl')
        # 不正JSONも完了応答として保存し、自動で再送しない。
        completed = {r['request_key'] for r in responses}
        for task in tasks:
            if task['request_key'] not in logged:
                append(path/'requests.jsonl', task)
            if dry_run or (task['request_key'] in completed and not force):
                continue
            # 呼出し直前にも監査し、評価用メタデータをproviderに渡さない。
            content = task['request']['prompt'].split('観測事実:\n', 1)[1]
            audit_task_input(json.loads(content) if task['representation'] == 'structured' else content)
            attempts = 0
            error_category = None
            while attempts < max_attempts:
                attempts += 1
                try:
                    if task['request_key'] in imported:
                        result = imported[task['request_key']]
                    elif isinstance(provider, ExternalProvider):
                        raise PermanentProviderError('外部応答がありません')
                    else:
                        manifest['provider_attempt_count'] += 1
                        atomic_json(manifest_path, manifest)
                        result = provider.generate(copy.deepcopy(task['request']), copy.deepcopy(decoding))
                    if not isinstance(result, LLMResponse) or not isinstance(result.raw_response, str):
                        raise PermanentProviderError('応答形式が不正です')
                    valid = validate_response(result.raw_response)
                    record = {k: task[k] for k in ('request_key', 'sample_id', 'condition', 'representation')}
                    record.update(raw_response=result.raw_response, valid=valid,
                                  parsed_result=json.loads(result.raw_response) if valid else None,
                                  attempt_count=attempts, error_category=None if valid else 'invalid_output',
                                  usage=safe_usage(result.usage), timestamp=datetime.now(timezone.utc).isoformat())
                    append(path/'responses.jsonl', record)
                    responses.append(record)
                    if not valid:
                        append(path/'failures.jsonl', {**record, 'raw_response': result.raw_response})
                    error_category = None
                    break
                except TransientProviderError:
                    error_category = 'transient_provider_error'
                except PermanentProviderError:
                    error_category = 'permanent_provider_error'
                except Exception:
                    error_category = 'unexpected_provider_error'
                # 例外文は認証情報やURLを含む可能性があるので保存しない。
                append(path/'failures.jsonl', {'request_key': task['request_key'], 'attempt_count': attempts,
                                               'error_category': error_category})
                if error_category != 'transient_provider_error':
                    break
                if attempts < max_attempts:
                    time.sleep(retry_delay)
        atomic_json(manifest_path, manifest)
        return write_scores(path, tasks, expected, responses, manifest)


def main():
    parser = argparse.ArgumentParser(description='Phase 5C分類実験。標準はpilot。openai指定時は実APIへ送信します')
    parser.add_argument('--input-dir', type=Path, required=True, help='Phase 5B出力先')
    parser.add_argument('--output', type=Path, required=True, help='実験結果の保存先（Git外）')
    parser.add_argument('--provider', choices=('mock', 'external', 'openai'), default='mock', help='mockは性能評価ではありません')
    parser.add_argument('--model', help='正確なモデル識別子。external/openaiでは必須')
    parser.add_argument('--external-provider', help='取り込む外部応答のprovider識別子')
    parser.add_argument('--responses-file', type=Path, help='request_key/raw_responseを持つ外部応答JSONL')
    parser.add_argument('--mode', choices=('pilot', 'full'), default='pilot', help='fullは明示指定時のみ')
    parser.add_argument('--dataset', choices=('clean', 'perturbations'), default='clean', help='清浄結果と摂動結果は分離')
    parser.add_argument('--conditions', default='a,b,c,d', help='カンマ区切りの条件')
    parser.add_argument('--representations', default='structured,ja', help='カンマ区切りの表現')
    parser.add_argument('--temperature', type=lambda v: None if v == 'api-default' else float(v),
                        default=argparse.SUPPRESS, help='生成温度。api-defaultで未指定。openai既定は未指定、他は0')
    parser.add_argument('--reasoning-effort', choices=('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'))
    parser.add_argument('--max-output-tokens', type=int)
    parser.add_argument('--seed', type=int, default=42, help='pilot選択seed')
    parser.add_argument('--provider-seed', type=int, help='providerが対応する場合の生成seed')
    parser.add_argument('--force', action='store_true', help='保存済み応答も明示的に再取得')
    parser.add_argument('--dry-run', action='store_true', help='構築・監査・保存のみ。モデル呼出しなし')
    args = parser.parse_args()
    if args.provider == 'external' and (not args.model or not args.external_provider):
        parser.error('externalには--modelと--external-providerが必要です')
    if args.provider == 'openai' and not args.model:
        parser.error('openaiには--modelが必要です')
    if args.provider == 'openai' and args.responses_file:
        parser.error('openaiでは--responses-fileを使用できません')
    if args.provider != 'openai' and (args.reasoning_effort is not None or args.max_output_tokens is not None):
        parser.error('--reasoning-effort/--max-output-tokensはopenai専用です')
    provider = (MockProvider(args.model or 'mock-constant-v1') if args.provider == 'mock' else
                OpenAIProvider(args.model) if args.provider == 'openai' else
                ExternalProvider(args.external_provider, args.model))
    try:
        report = run_experiment(args.input_dir, args.output, provider, mode=args.mode,
                                conditions=tuple(args.conditions.split(',')),
                                representations=tuple(args.representations.split(',')), dataset=args.dataset,
                                temperature=getattr(args, 'temperature', None if args.provider == 'openai' else 0.0),
                                seed=args.seed, provider_seed=args.provider_seed,
                                reasoning_effort=args.reasoning_effort, max_output_tokens=args.max_output_tokens,
                                force=args.force, dry_run=args.dry_run, responses_file=args.responses_file)
    except (ValueError, KeyError, OSError, TypeError, PermanentProviderError):
        parser.error('実験に失敗しました。入力・設定・出力先を確認してください（秘密保護のため例外本文は表示しません）')
    print(f'実験処理完了: {len(report["settings"])}設定 / 保存先: {args.output}')
    print('dry-runまたはmockの結果を実LLM性能と解釈しないでください。費用は取得不可です。')


if __name__ == '__main__':
    main()

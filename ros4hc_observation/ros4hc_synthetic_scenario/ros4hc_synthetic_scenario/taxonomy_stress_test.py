"""Phase 5O blind taxonomy exploration; never modifies production taxonomy."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess

from . import task_router as router
from . import evidence_grounded_interpretation as base
from .connector_registry import CONNECTOR_NAMES, build_context
from .llm_providers import OpenAIProvider, TransientProviderError

STATUSES = ('well_mapped', 'forced_mapping', 'unmapped_but_understood', 'ambiguous',
            'unsupported_capability', 'true_unknown')
# This literal is deliberately independent of router constants and examples.
GENERATOR = '''研究者がAI研究支援システムに自然に相談する日本語の質問を12件生成する。
システムの分類や実装は知らないものとする。正解ラベルや分類名は付けない。
healthcare, rehabilitation, human movement, robotics, wearable sensing,
environmental sensing, sleep, cardiovascular research, respiratory research,
aging / frailty, experimental science, device evaluation, multimodal sensing,
human-robot interaction を幅広く扱い、特定領域へ偏らせない。
具体的、open-ended、目的だけ明確で方法不明、方法は明確で目的曖昧、複数目的、時間変化、
データ解析、再現実験、仮説探索、未解決問題、データ品質、再現性、倫理・研究手続き、
実現が難しそうな要求、曖昧な要求、領域横断を混ぜる。自然な英語研究用語も可。
各質問は独立した実際の相談文にし、似た言い換えは避ける。指定IDを順に使う。
'''
QUESTION_SCHEMA = base.obj({'question_id': {'type': 'string'},
    'generation_batch': {'type': 'integer'}, 'user_request': {'type': 'string'}})
AUDIT_SCHEMA = base.obj({
    'question_id': {'type': 'string'}, 'mapping_status': {'type': 'string', 'enum': list(STATUSES)},
    'assigned_task_type': {'type': 'string'}, 'natural_mapping': {'type': 'boolean'},
    'intent_preserved': {'type': 'boolean'}, 'reason_ja': {'type': 'string'},
    **{k: base.strings() for k in ('missing_task_concept', 'missing_information_type',
                                  'candidate_new_task', 'candidate_new_information')},
    'needs_human_review': {'type': 'boolean'}})
DEFINITIONS = '''well_mapped:既存分類と情報種別で意図を無理なく表現できる。
forced_mapping:近い分類への押し込みで重要な意図が失われる。
unmapped_but_understood:意図は明確だが自然な分類がない。
ambiguous:質問自体が複数解釈可能で確認が必要。
unsupported_capability:表現できても必要な情報源・機能が存在しない。
true_unknown:意味ある研究taskとして解釈できない。
形式的validityではなく意味の自然さを判断。曖昧さが主ならambiguous、分類欠落が主なら
unmapped_but_understood、分類の歪みが主ならforced_mappingを優先。
情報源不足だけで分類欠落としない。候補は提案だけで追加しない。
判断困難・複合的な場合needs_human_review=true。理由に失われた意図と能力不足を区別して記す。
'''


def independence_audit(prompts):
    # Inspection only: forbidden values never feed generation construction.
    forbidden = (*router.TASK_TYPES, *router.INFORMATION, *CONNECTOR_NAMES,
                 'TASK_TYPES', 'INFORMATION', 'REQUIRES', *[q for _, q in router.EXAMPLES])
    violations = [s for s in forbidden if any(s.casefold() in p.casefold() for p in prompts)]
    if violations:
        raise ValueError('Generator independence violation')
    return {'passed': True, 'checked_strings': len(forbidden),
            'prompt_sha256': [hashlib.sha256(p.encode()).hexdigest() for p in prompts]}


def generator_prompts():
    return [GENERATOR + f'\nbatch={b}; IDs Q{(b-1)*12+1:03d}〜Q{b*12:03d}。\n'
            + ('前batchと重ならない新しい状況を作る。' if b > 1 else '') for b in range(1, 5)]


def validate_questions(rows):
    import jsonschema
    if len(rows) != 48 or {r['question_id'] for r in rows} != {f'Q{i:03d}' for i in range(1, 49)}:
        raise ValueError('48 unique expected IDs required')
    seen = set()
    for i, r in enumerate(rows):
        jsonschema.validate(r, QUESTION_SCHEMA)
        normalized = ''.join(r['user_request'].split()).casefold().rstrip('。?!？！')
        if not normalized or normalized in seen or r['generation_batch'] != i // 12 + 1:
            raise ValueError('Duplicate/empty question or incorrect batch')
        seen.add(normalized)
    return rows


def validate_audits(rows, routed):
    import jsonschema
    expected = {r['question_id']: r for r in routed}
    if len(rows) != len(expected) or {r['question_id'] for r in rows} != set(expected):
        raise ValueError('Audit IDs mismatch')
    for r in rows:
        jsonschema.validate(r, AUDIT_SCHEMA)
        task = expected[r['question_id']].get('task_spec', {})
        if r['assigned_task_type'] != task.get('task_type', 'router_failure'):
            raise ValueError('Assigned task mismatch')
    return rows


def aggregate(routed, audits):
    n = len(routed)
    tasks = Counter(r.get('task_spec', {}).get('task_type', 'router_failure') for r in routed)
    freq = lambda values: dict(sorted(Counter(values).items()))
    stats = dict(question_count=n, task_type_frequency={k: {'count': v, 'percentage': v*100/n}
        for k, v in sorted(tasks.items())}, unknown_rate=tasks['unknown']/n,
        router_failure_rate=sum(r['status'] != 'success' for r in routed)/n,
        ambiguities_rate=sum(bool(r.get('task_spec', {}).get('ambiguities')) for r in routed)/n,
        required_information_frequency=freq(v for r in routed for v in r.get('task_spec', {}).get('required_information', [])),
        required_information_counts={r['question_id']: len(r.get('task_spec', {}).get('required_information', [])) for r in routed},
        unavailable_source_frequency=freq(v for r in routed for v in r.get('context', {}).get('unavailable_sources', [])),
        coverage_gap_frequency=freq(v for r in routed for v in r.get('context', {}).get('coverage_gaps', [])),
        empty_targets=[r['question_id'] for r in routed if not r.get('task_spec', {}).get('target_concepts')],
        excessive_targets=[r['question_id'] for r in routed if len(r.get('task_spec', {}).get('target_concepts', [])) > 8])
    # Exploratory threshold, not evidence of semantic error.
    catch = [k for k, v in tasks.items() if k != 'router_failure' and v >= 12 and v/n >= .25]
    stats.update(catch_all_suspicions=catch, catch_all_rule='count>=12 and share>=25%; excessive targets>8',
                 repeated_patterns=freq(r['task_spec']['task_type']+'|'+','.join(sorted(r['task_spec']['required_information']))
                    for r in routed if 'task_spec' in r))
    summaries = []
    for field in ('candidate_new_task', 'candidate_new_information'):
        groups = defaultdict(list)
        for a in audits:
            for c in sorted(set(a[field])):
                groups[c].append(a)
        summaries.append([dict(candidate=c, supporting_question_ids=[a['question_id'] for a in aa],
            count=len(aa), examples=[next(r['user_request'] for r in routed if r['question_id']==a['question_id']) for a in aa[:3]],
            reason=[a['reason_ja'] for a in aa], repeated=len(aa)>1) for c, aa in sorted(groups.items())])
    by_id = {r['question_id']: r for r in routed}
    queue = []
    for a in audits:
        score = (4*(a['mapping_status'] in ('forced_mapping', 'unmapped_but_understood'))
                 + 2*bool(a['candidate_new_task'] or a['candidate_new_information'])
                 + 2*a['needs_human_review'] + (a['assigned_task_type'] in catch)
                 + 2*(a['assigned_task_type']=='unknown' and a['mapping_status']!='true_unknown')
                 + (not a['intent_preserved']))
        if score:
            queue.append(dict(**a, user_request=by_id[a['question_id']]['user_request'],
                              priority=score, human_judgment='', reviewer_note=''))
    queue = sorted(queue, key=lambda a: (-a['priority'], a['question_id']))[:20]
    return stats, *summaries, queue


def save_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))


def run(output, dry_run=False):
    output = Path(output).expanduser().resolve()
    parent = next(p for p in (output, *output.parents) if p.exists())
    if subprocess.run(['git', '-C', str(parent), 'rev-parse', '--show-toplevel'], capture_output=True).returncode == 0:
        raise ValueError('Output must be Git-external')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty')
    prompts = generator_prompts()
    independence = independence_audit(prompts)
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(phase='5O', model='gpt-6-astra', reasoning_effort='medium', store=False,
        tools=False, web=False, temperature=False, dry_run=dry_run,
        estimated_api_calls={'generator':4, 'router':48, 'auditor':4, 'total':56},
        router_transient_retry_limit=1, hard_call_cap=60, actual_api_calls=Counter(),
        token_usage={k: Counter() for k in ('generator','router','auditor')}, status='running',
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        independence_audit=independence,
        production_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [Path(router.__file__), Path(__file__).with_name('connector_registry.py'), Path(__file__).with_name('grounded_research_assistant.py')]},
        context_scope='Existing build_context with no supplied observations or machine adapter; local sources only')
    def checkpoint():
        base.save(output/'manifest.json', manifest)
    checkpoint()
    base.save(output/'generator_prompt.json', {'prompts':prompts, 'schema':QUESTION_SCHEMA})
    provider = OpenAIProvider('gpt-6-astra', timeout=600)
    def call(stage, prompt, schema, retry=False):
        for attempt in range(2 if retry else 1):
            if sum(manifest['actual_api_calls'].values()) >= 60:
                raise ValueError('Call cap')
            manifest['actual_api_calls'][stage] += 1
            checkpoint()
            try:
                response = provider.generate(dict(prompt=prompt, output_schema=schema), {'reasoning_effort':'medium'})
                manifest['token_usage'][stage].update(response.usage)
                checkpoint()
                return response.raw_response, response.usage
            except TransientProviderError:
                if not retry or attempt:
                    raise
    try:
        questions=[]
        for b,p in enumerate(prompts,1):
            if dry_run:
                batch=[dict(question_id=f'Q{i:03d}', generation_batch=b,
                            user_request=f'配線検証専用fixture {i}：研究データを検討したい') for i in range((b-1)*12+1,b*12+1)]
            else:
                raw,_=call('generator',p,base.obj({'questions':{'type':'array','items':QUESTION_SCHEMA,'minItems':12,'maxItems':12}}))
                batch=json.loads(raw)['questions']
            questions.extend(batch)
            save_jsonl(output/'generated_questions.jsonl', questions)
        validate_questions(questions)
        routed=[]
        for q in questions:
            row=dict(**q, status='pending', token_usage={})
            try:
                req=router.make_request(q['user_request'])
                if dry_run:
                    raw=json.dumps(router.fixture_task('B'))
                else:
                    raw,row['token_usage']=call('router',req['prompt'],req['output_schema'],retry=True)
                task=router.validate_task(router.parse_task(raw))
                row.update(task_spec=task, status='success')
                row['context']=build_context(q['user_request'],task)
            except Exception as exc:
                row.update(status='failure',error_type=type(exc).__name__)
            routed.append(row)
            save_jsonl(output/'router_results.jsonl',routed)
            print(q['question_id'], row['status'],flush=True)
        audits=[]
        for start in range(0,48,12):
            batch=routed[start:start+12]
            if dry_run:
                aa=[dict(question_id=r['question_id'], mapping_status='well_mapped',
                    assigned_task_type=r['task_spec']['task_type'], natural_mapping=True,intent_preserved=True,
                    reason_ja='配線検証fixture。意味評価ではない。',missing_task_concept=[],missing_information_type=[],
                    candidate_new_task=[],candidate_new_information=[],needs_human_review=False) for r in batch]
            else:
                prompt=DEFINITIONS+'\n現在の分類定義（production parserそのもの）:\n'+router.INSTRUCTIONS+'\n'+json.dumps({
                    'task_types':router.TASK_TYPES,'information':router.INFORMATION,'requires':router.REQUIRES,
                    'capability_context':manifest['context_scope'],'items':batch},ensure_ascii=False)
                raw,_=call('auditor',prompt,base.obj({'audits':{'type':'array','items':AUDIT_SCHEMA,'minItems':len(batch),'maxItems':len(batch)}}))
                aa=json.loads(raw)['audits']
            validate_audits(aa,batch)
            audits.extend(aa)
            save_jsonl(output/'audit_results.jsonl',audits)
        stats,tasks,information,queue=aggregate(routed,audits)
        base.save(output/'router_stats.json',stats)
        base.save(output/'taxonomy_candidate_summary.json',tasks)
        base.save(output/'information_candidate_summary.json',information)
        save_jsonl(output/'human_review_queue.jsonl',queue)
        categories=dict(Counter(a['mapping_status'] for a in audits))
        final=dict(question_count=len(questions), audit_count=len(audits), audit_categories=categories,
            independence_audit=independence, human_review_count=len(queue), production_unchanged=all(
                hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()==h for n,h in manifest['production_hashes'].items()),
            no_taxonomy_revision=True, formal_accuracy_evaluation=False)
        base.save(output/'final_audit.json',final)
        manifest['status']='fixture_complete' if dry_run else 'complete'
        checkpoint()
        lines=['# Phase 5O Blind Question Wall-Ball Pilot','',
            '目的：固定taxonomyと情報allowlistの表現上の弱点を探索する。生成時には分類・Connector・既存例を渡さず、生成後にRouterを実行した。',
            'これはLLM生成質問＋LLM Auditorによるstress testであり、Task routing accuracyの正式評価ではない。',
            'dry-run='+str(dry_run)+'。fixtureは意味評価に使用できない。',
            '領域・styleはgenerator promptで多様化を指示。実際の網羅性・代表性を保証しない。',
            '## 48 questions']
        lines += [f"- {q['question_id']} (batch {q['generation_batch']}): {q['user_request']}" for q in questions]
        for title,value in [('Task distribution / deterministic indicators',stats),('LLM audit categories',categories),
                            ('新task候補（count>1は反復、同義語の自動統合なし）',tasks),('新information候補',information),
                            ('Human review対象',[q['question_id'] for q in queue]),('API calls',manifest['actual_api_calls']),
                            ('Token usage',manifest['token_usage'])]:
            lines += ['## '+title,'```json',json.dumps(value,ensure_ascii=False,indent=2),'```']
        for status in ('forced_mapping','unmapped_but_understood','unsupported_capability'):
            lines+=['## '+status]
            lines += [a['question_id']+': '+a['reason_ja'] for a in audits if a['mapping_status']==status]
        lines += ['## Limitations / 判断',
            'LLM Auditorはground truthではない。固定4batchの生成は独立callであり、batch間の意味的重複は人間による確認が必要。',
            '複数意図を単一statusへ要約している。Connector不足は提供入力とローカル知識の条件に依存し、taxonomy不足とは異なる。',
            '候補は表記一致で集計。catch-all閾値は探索用であり、集中だけで誤分類とはいえない。',
            '現taxonomyの即時変更は推奨しない。human reviewと追加壁打ちで候補・同義語・境界を確認してから改訂を検討する。']
        base.save(output/'report_ja.md','\n\n'.join(lines)+'\n',text=True)
        return manifest
    except Exception as exc:
        manifest.update(status='failed',error_type=type(exc).__name__)
        checkpoint()
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    result=run(a.output,a.dry_run)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    main()

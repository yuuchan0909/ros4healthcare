"""Phase 5Q targeted boundary exploration; production definitions remain immutable."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess

from . import task_router as router
from . import evidence_grounded_interpretation as base
from . import mapping_only_audit as previous
from . import taxonomy_stress_test as blind
from .llm_providers import OpenAIProvider, TransientProviderError

# Generator text is independent of production definitions and previous questions.
THEMES = {
    'A': '既存データからどこまで言えるか、何を結論してはいけないか、バイアスや欠測を踏まえた解釈',
    'B': '観測や既存知識から新しい仮説候補を作る',
    'C': '既存研究から未解決問題・不足研究・次の問いを見つける',
    'D': 'センサデータ品質、測定誤差、欠測、同期ズレ、artifact、信頼性を評価する',
    'E': '既存研究の追試条件、再現可能性、手順不足を評価する',
    'F': '適切な解析方法、因果と相関の区別、統計設計を相談する',
    'G': '同意、継続同意、privacy、研究手続き、参加者保護を相談する',
    'H': '独立した複数の主要目的を含む。センサ選択と実験計画、自動実験と解析計画、文献探索と仮説生成、状態解釈と追加測定、リスク仮説と介入計画など',
    'I': '時間変化、before/after、baseline comparison、trend、時系列因果を相談する',
    'J': '複数領域をまたぐ。rehabilitationとrobotics、sleepとgait、cardiovascularとwearable sensingなど',
}
GENERATOR = '''研究者が自然に相談する日本語の質問を12件作る。通常質問の網羅ではなく、
単一の目的では捉えにくい相談、近接した目的の境界、既存の分類で不足しそうな相談を意図的に作る。
システムの分類名や実装は知らないものとし、正解ラベルは付けない。
healthcare, rehabilitation, human movement, robotics, wearable sensing, environmental sensing,
sleep, cardiovascular, respiratory, aging/frailty, experimental science, device evaluation,
multimodal sensing, human-robot interactionを多様に扱う。自然な研究者の具体的な相談文にする。
割当テーマは主テーマであり他の要素との重なりを許す。質問の言い換えを繰り返さない。
指定されたquestion_id, generation_batch, boundary_themeをそのまま保存する。
'''
QUESTION_SCHEMA = base.obj({
    'question_id': {'type': 'string'}, 'generation_batch': {'type': 'integer'},
    'boundary_theme': {'type': 'string', 'enum': list(THEMES)}, 'user_request': {'type': 'string'}})
MULTI = ('not_multi_intent', 'well_represented', 'partially_represented', 'major_intent_lost')
TEMPORAL = ('not_applicable', 'preserved', 'partially_preserved', 'lost')
AUDIT_SCHEMA = base.obj({**previous.AUDIT_SCHEMA['properties'],
    'candidate_new_information': base.strings(),
    'multi_intent_preservation': {'type': 'string', 'enum': list(MULTI)},
    'multi_intent_reason_ja': {'type': 'string'},
    'temporal_intent_preservation': {'type': 'string', 'enum': list(TEMPORAL)},
    'temporal_reason_ja': {'type': 'string'},
    'information_intent_preserved': {'type': 'boolean'},
    'information_reason_ja': {'type': 'string'},
    'limitation_source': {'type': 'string', 'enum': ['none', 'taxonomy', 'information_vocabulary',
        'router_extraction', 'single_task_representation', 'unclear']},
    'domain': {'type': 'string'},
})
POLICY = previous.POLICY + '''
複合目的はtask_typeが1個というだけではforcedにしない。required_informationとrequested_outputs等で
副次目的を保持できればwell_mapped。主要意図の消失、重要な区別の喪失、downstream routingに
影響する意味の欠落がある場合のみforcedとする。H以外でも複合目的なら複合評価を行う。
multi_intent_preservation: not_multi_intent / well_represented / partially_represented / major_intent_lost。
temporal_scopeは元質問のrecent、before/after、longitudinal、historical baseline、future等の関係や
基準時点を保持しているかを独立評価する。他のフィールドが補完する場合も理由に示す。
失われた時間次元はmissing_intent_dimensionsへ記す。単一stringという形式だけで不足と判定しない。
required_informationは情報要求の意味表現だけを見る。品質、統計、倫理、再現性、結論の限界、
研究の未解決点が既存項目や他フィールドで自然に保持されるかを確認する。情報源の有無は無視。
新information候補は情報要求自体が表現できない場合のみcandidate_new_informationへ。
新task候補はforced/unmappedの場合のみ。細分化しすぎず同義概念は統合した英語snake_case名を使う。
limitation_sourceで分類不足、情報語彙不足、個別抽出漏れ、単一task表現の限界を区別する。
domainは元質問の主領域を短く示す。well_mappedでも時間や情報の小さな欠落は独立欄に記録できる。
'''
ALIASES = {**previous.ALIASES,
    # Observed auditor synonym, consolidated after generation; never sent to Generator.
    'research_inference_evaluation': 'inference_appraisal',
    'critical_inference_appraisal': 'inference_appraisal',
    'evidence_interpretation_validity': 'inference_appraisal',
    'inference_validity': 'inference_appraisal',
    'research_gap_identification': 'research_gap_discovery',
    'research_gap_analysis': 'research_gap_discovery',
    'statistical_analysis_planning': 'statistical_analysis_reasoning',
    'analytical_reasoning': 'statistical_analysis_reasoning',
    'statistical_reasoning': 'statistical_analysis_reasoning',
    'research_ethics_assessment': 'ethics_governance_assessment',
    'study_governance': 'ethics_governance_assessment',
    'ethics_planning': 'ethics_governance_assessment',
    'measurement_validity_assessment': 'data_quality_assessment',
    'data_validity_assessment': 'data_quality_assessment',
}


def plan():
    return [dict(question_id=f'Q{i+1:03d}', generation_batch=i//12+1,
                 boundary_theme=list(THEMES)[i % 10]) for i in range(48)]


def generator_prompts():
    return [GENERATOR+'\n境界テーマ:\n'+json.dumps(THEMES, ensure_ascii=False)+
            '\n今回の割当:\n'+json.dumps(plan()[s:s+12], ensure_ascii=False)
            for s in range(0, 48, 12)]


def independence_audit(prompts):
    result = blind.independence_audit(prompts)
    if any('inference_appraisal' in p.casefold() for p in prompts):
        raise ValueError('Candidate name leaked')
    result.update(candidate_name_absent=True, previous_questions_supplied=False,
                  taxonomy_used_to_construct_prompts=False)
    return result


def validate_questions(rows):
    import jsonschema
    if len(rows) != 48:
        raise ValueError('48 questions required')
    seen = set()
    for q, expected in zip(rows, plan()):
        jsonschema.validate(q, QUESTION_SCHEMA)
        if any(q[k] != v for k, v in expected.items()):
            raise ValueError('Question ID/batch/theme mismatch')
        normalized = ''.join(q['user_request'].split()).casefold().rstrip('。?!？！')
        if not normalized or normalized in seen:
            raise ValueError('Duplicate/empty question')
        seen.add(normalized)
    return rows


def validate_audits(rows, items):
    import jsonschema
    # Phase 5P validates ID, task identity and well_mapped consistency.
    old_keys = set(previous.AUDIT_SCHEMA['properties'])
    previous.validate_audits([{k: a[k] for k in old_keys} for a in rows], items)
    for a in rows:
        jsonschema.validate(a, AUDIT_SCHEMA)
        if a['candidate_new_task'] and a['mapping_status'] not in (
                'forced_mapping', 'unmapped_but_understood'):
            raise ValueError('Task candidate needs forced/unmapped support')
        if a['multi_intent_preservation'] == 'major_intent_lost' and a['mapping_status'] == 'well_mapped':
            raise ValueError('Major intent lost cannot be well_mapped')
        if a['temporal_intent_preservation'] in ('lost', 'partially_preserved') and not a['missing_intent_dimensions']:
            raise ValueError('Temporal loss must name missing dimension')
    return rows


def audit_request(items):
    req = previous.make_request(items)
    req['prompt'] = req['prompt'].replace(previous.POLICY, POLICY, 1)
    req['output_schema'] = base.obj({'audits': {'type': 'array', 'items': AUDIT_SCHEMA,
                                               'minItems': len(items), 'maxItems': len(items)}})
    return req


def fixture_audits(items):
    return [dict(**a, candidate_new_information=[], multi_intent_preservation='not_multi_intent',
        multi_intent_reason_ja='fixture', temporal_intent_preservation='not_applicable',
        temporal_reason_ja='fixture', information_intent_preserved=True,
        information_reason_ja='fixture', limitation_source='none', domain='fixture')
        for a in previous.fixture(items)]


def strength(n):
    return 'strong' if n >= 4 else 'emerging' if n >= 2 else 'weak'


def canonical(name):
    name = name.strip().lower().replace(' ', '_').replace('-', '_')
    seen = set()
    while name in ALIASES and name not in seen:
        seen.add(name)
        name = ALIASES[name]
    return name


def aggregate(questions, items, audits):
    validate_questions(questions)
    validate_audits(audits, items)
    audits = sorted(audits, key=lambda a: a['question_id'])
    qs = {q['question_id']: q for q in questions}
    summaries = []
    for field in ('candidate_new_task', 'candidate_new_information'):
        groups = defaultdict(list)
        for a in audits:
            for c in sorted({canonical(v) for v in a[field]}):
                groups[c].append(a)
        summaries.append([dict(canonical_candidate=c, supporting_question_ids=[a['question_id'] for a in aa],
            count=len(aa), strength=strength(len(aa)),
            boundary_themes=sorted({qs[a['question_id']]['boundary_theme'] for a in aa}),
            domains=sorted({a['domain'] for a in aa}),
            examples=[qs[a['question_id']]['user_request'] for a in aa[:3]],
            original_labels=sorted({v for a in aa for v in a[field] if canonical(v)==c}),
            reason_ja='同義語表で統合。反復件数だけでtaxonomy変更は決定しない。',
            auditor_reasons_ja=[a['reason_ja'] for a in aa]) for c, aa in sorted(groups.items())])
    tasks = {}
    for t in sorted({a['assigned_task_type'] for a in audits}):
        aa = [a for a in audits if a['assigned_task_type'] == t]
        failed = [a for a in aa if a['mapping_status'] in ('forced_mapping', 'unmapped_but_understood')]
        counts = {s: sum(a['mapping_status']==s for a in aa) for s in previous.STATUSES}
        tasks[t] = dict(assigned_count=len(aa), mapping_status_counts=counts,
            boundary_theme_breakdown=dict(Counter(qs[a['question_id']]['boundary_theme'] for a in aa)),
            forced_themes=sorted({qs[a['question_id']]['boundary_theme'] for a in failed}),
            forced_candidates=sorted({canonical(c) for a in failed for c in a['candidate_new_task']}),
            catch_all_risk=len({canonical(c) for a in failed for c in a['candidate_new_task']})>=2,
            audits=aa)
    multi_rows = [dict(**a, boundary_theme=qs[a['question_id']]['boundary_theme']) for a in audits
                  if a['multi_intent_preservation'] != 'not_multi_intent' or qs[a['question_id']]['boundary_theme']=='H']
    multi = dict(count=len(multi_rows), counts=dict(Counter(a['multi_intent_preservation'] for a in multi_rows)),
                 designated_H_count=sum(q['boundary_theme']=='H' for q in questions), items=multi_rows)
    temporal = dict(counts=dict(Counter(a['temporal_intent_preservation'] for a in audits)),
        issues=[a for a in audits if a['temporal_intent_preservation'] in ('lost','partially_preserved')])
    queue = []
    for a in audits:
        score = (8*(a['mapping_status'] in ('forced_mapping','unmapped_but_understood'))+
            6*(a['multi_intent_preservation']=='major_intent_lost')+
            4*(a['temporal_intent_preservation'] in ('lost','partially_preserved'))+
            3*bool(a['candidate_new_task'])+3*bool(a['candidate_new_information'])+
            2*(a['assigned_task_type']=='research_planning' and tasks['research_planning']['catch_all_risk'])+
            a['needs_human_review'])
        if score:
            queue.append(dict(**a, user_request=qs[a['question_id']]['user_request'],
                boundary_theme=qs[a['question_id']]['boundary_theme'], priority=score,
                human_mapping_status='', human_preferred_task='', human_missing_information='', human_note=''))
    queue = sorted(queue, key=lambda a: (-a['priority'], a['question_id']))[:20]
    stats = dict(question_count=48, boundary_theme_distribution=dict(Counter(q['boundary_theme'] for q in questions)),
        task_distribution=dict(Counter(a['assigned_task_type'] for a in audits)),
        mapping_status_distribution={s:sum(a['mapping_status']==s for a in audits) for s in previous.STATUSES},
        task_overload=tasks, limitation_source_distribution=dict(Counter(a['limitation_source'] for a in audits)),
        information_intent_not_preserved=[a['question_id'] for a in audits if not a['information_intent_preserved']],
        human_review_queue_count=len(queue))
    return stats, *summaries, multi, temporal, queue


def report(output, questions, audits, manifest, result):
    stats, tasks, info, multi, temporal, queue = result
    judgment = ('D' if sum(a['limitation_source']=='single_task_representation' for a in audits)>=4 else
                'B' if any(c['strength'] in ('strong','emerging') for c in tasks) else
                'C' if info else 'A')
    lines = ['# Phase 5Q Boundary / Multi-intent Task Taxonomy Stress Test',
        '目的：single task_typeが苦手とする境界・複合目的・分類の端を探索する。Phase 5Pは通常のblind質問を再監査したが、今回は難しい相談を意図的に生成する。',
        'これは意図的に難しい質問を生成したstress testであり、自然な研究質問全体の分布を表すものではない',
        '正式なrouting accuracy評価ではない',
        '生成設計：独立した4 batches×12問。10境界テーマを4〜5問ずつ配分。Generatorにはテーマ、領域多様性、自然な文体、ID割当のみを渡す。taxonomy・情報一覧・Connector・過去質問・新task候補名は渡さない。',
        'fixture='+str(manifest['dry_run'])+'。fixtureは意味評価ではない。', '## 48 questions一覧']
    lines += [f"- {q['question_id']} [{q['boundary_theme']}] {q['user_request']}" for q in questions]
    sections = [('boundary theme distribution',stats['boundary_theme_distribution']),
        ('task distribution',stats['task_distribution']),('mapping status distribution',stats['mapping_status_distribution']),
        ('research_planning内部分析',stats['task_overload'].get('research_planning',{})),
        ('experiment_planning内部分析',stats['task_overload'].get('experiment_planning',{})),
        ('multi-intent結果',multi),('temporal_scope結果',temporal),
        ('repeated new task candidates / strong・emerging・weak',tasks),
        ('repeated new information candidates / strong・emerging・weak',info),
        ('human review対象',[q['question_id'] for q in queue]),('API calls',manifest['actual_api_calls']),
        ('token usage',manifest['token_usage'])]
    for title, value in sections:
        lines += ['## '+title, '```json', json.dumps(value,ensure_ascii=False,indent=2), '```']
    for s in ('forced_mapping','unmapped_but_understood'):
        lines += ['## '+s]+[a['question_id']+'：'+a['reason_ja'] for a in audits if a['mapping_status']==s]
    lines += ['## 判断・limitations', '暫定判断：'+judgment+'。集計上の候補反復と欠落原因による探索的判断であり、最終決定は人間review後。',
        'LLM生成と単一LLM監査でありground truthではない。テーマは主テーマで相互排他的な意味分類ではない。文字正規化による重複拒否は意味的重複を保証しない。候補は明示同義語表で統合し、表外の同義・粒度は人間reviewが必要。件数の強さは重要性や妥当性を保証しない。',
        '分類表現の不足とRouter抽出漏れは区別して記録したが、同一モデルによる判断誤差がある。新information候補も能力・情報源の存在を評価しない。',
        'taxonomyは今変更しない。人間reviewで候補・複合意図・時間基準を確認してから検討する。commit/pushは実施していない。']
    base.save(output/'report_ja.md','\n\n'.join(lines)+'\n',text=True)
    return judgment


def run(output, dry_run=False):
    output = Path(output).expanduser().resolve()
    parent = next(p for p in (output,*output.parents) if p.exists())
    if subprocess.run(['git','-C',str(parent),'rev-parse','--show-toplevel'],capture_output=True).returncode==0:
        raise ValueError('Output must be Git-external')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output must be empty')
    prompts = generator_prompts()
    independence = independence_audit(prompts)
    protected = [Path(router.__file__),Path(router.__file__).with_name('connector_registry.py'),
                 Path(router.__file__).with_name('grounded_research_assistant.py')]
    manifest = dict(phase='5Q',model='gpt-6-astra',reasoning_effort='medium',store=False,tools=False,
        web=False,temperature_specified=False,dry_run=dry_run,status='running',
        actual_api_calls=Counter(),token_usage={k:Counter() for k in ('generator','router','auditor')},
        estimated_api_calls=dict(generator=4,router=48,auditor=4,total=56),hard_call_cap=60,
        router_transient_retry_limit=1,independence_audit=independence,
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        protected_hashes={str(p):previous.digest(p) for p in protected},synonym_aliases=ALIASES,
        acquisition_calls=0, context_build_calls=0, failures=[])
    output.mkdir(parents=True,exist_ok=True)
    def checkpoint():
        base.save(output/'manifest.json',manifest)
    checkpoint()
    base.save(output/'generator_prompt.json',dict(prompts=prompts,schema=QUESTION_SCHEMA,independence_audit=independence))
    provider = None if dry_run else OpenAIProvider('gpt-6-astra',timeout=600)
    def call(stage, req, retry=False):
        for attempt in range(2 if retry else 1):
            if sum(manifest['actual_api_calls'].values())>=60:
                raise ValueError('Call cap')
            manifest['actual_api_calls'][stage]+=1
            checkpoint()
            try:
                response=provider.generate(req,{'reasoning_effort':'medium'})
                manifest['token_usage'][stage].update(response.usage)
                checkpoint()
                return response.raw_response,response.usage
            except TransientProviderError:
                manifest['failures'].append(dict(stage=stage,attempt=attempt+1,error_type='TransientProviderError'))
                checkpoint()
                if not retry or attempt:
                    raise
    try:
        questions=[]
        for b,prompt in enumerate(prompts,1):
            if dry_run:
                batch=[dict(**q,user_request='配線fixture '+q['question_id']) for q in plan()[(b-1)*12:b*12]]
            else:
                raw,_=call('generator',dict(prompt=prompt,output_schema=base.obj({'questions':{
                    'type':'array','items':QUESTION_SCHEMA,'minItems':12,'maxItems':12}})))
                batch=json.loads(raw)['questions']
            questions.extend(batch)
            previous.save_jsonl(output/'generated_questions.jsonl',questions)
            print('Generated',len(questions),flush=True)
        validate_questions(questions)
        items=[]
        routed=[]
        for q in questions:
            row=dict(**q,status='pending',token_usage={})
            try:
                req=router.make_request(q['user_request'])
                if dry_run:
                    raw=json.dumps(router.fixture_task('B'))
                else:
                    raw,row['token_usage']=call('router',req,retry=True)
                task=router.validate_task(router.parse_task(raw))
                row.update(status='success',task_spec=task)
                items.append(dict(question_id=q['question_id'],user_request=q['user_request'],task_spec=task))
            except Exception as exc:
                row.update(status='failure',error_type=type(exc).__name__)
            routed.append(row)
            previous.save_jsonl(output/'router_results.jsonl',routed)
            print(q['question_id'],row['status'],flush=True)
        if len(items)!=48:
            raise ValueError('Router failures; inspect saved results before audit')
        audits=[]
        for s in range(0,48,12):
            batch=items[s:s+12]
            if dry_run:
                aa=fixture_audits(batch)
            else:
                raw,_=call('auditor',audit_request(batch))
                aa=json.loads(raw)['audits']
            validate_audits(aa,batch)
            audits.extend(aa)
            previous.save_jsonl(output/'mapping_audit_results.jsonl',audits)
            print('Audited',len(audits),flush=True)
        result=aggregate(questions,items,audits)
        stats,tasks,info,multi,temporal,queue=result
        for name,value in [('mapping_stats',stats),('task_candidate_summary',tasks),
                ('information_candidate_summary',info),('multi_intent_analysis',multi),('temporal_scope_analysis',temporal)]:
            base.save(output/(name+'.json'),value)
        previous.save_jsonl(output/'human_review_queue.jsonl',queue)
        unchanged=all(previous.digest(Path(p))==h for p,h in manifest['protected_hashes'].items())
        deterministic=result==aggregate(questions,items,list(reversed(audits)))
        final=dict(passed=unchanged and deterministic,question_count=48,audit_count=48,
            generator_independence=independence,production_unchanged=unchanged,
            deterministic_aggregation=deterministic,formal_routing_accuracy_evaluation=False,
            no_taxonomy_revision=True,mapping_only=True,human_review_count=len(queue),
            calls_within_cap=sum(manifest['actual_api_calls'].values())<=60)
        manifest['status']='fixture_complete' if dry_run else 'complete'
        final['provisional_judgment']=report(output,questions,audits,manifest,result)
        base.save(output/'final_audit.json',final)
        checkpoint()
        return manifest
    except Exception as exc:
        manifest.update(status='failed',error_type=type(exc).__name__)
        checkpoint()
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    result=run(args.output,args.dry_run)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()

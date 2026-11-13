"""Fixed pilot protocol, isolation, failures, budget and blinded audit; zero network."""
import copy
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from ros4hc_synthetic_scenario import end_to_end_evaluation as e
from ros4hc_synthetic_scenario import machine_knowledge as mk
from ros4hc_synthetic_scenario.llm_providers import LLMResponse


@pytest.fixture
def source(tmp_path):
    root = tmp_path/'source'
    root.mkdir()
    raw = b'<article><body><sec><title>Methods</title><p>Quadruped CPG parameter tuning procedure.</p><p>CPG quadruped statistical analysis measurement.</p></sec></body></article>'
    xml = root/'paper.xml'
    xml.write_bytes(raw)
    _, paragraphs, _ = mk.segment(ET.fromstring(raw))
    mk.save(root/'source_document.json',dict(doi=mk.DOI,pmcid=mk.PMCID,title='Quadruped CPG study',
        source_path=str(xml),source_sha256=mk.digest(raw),integrity_verified=True))
    mk.jsonl(root/'source_segments.jsonl',paragraphs)
    records=[]
    for i,typ in enumerate(('experimental_method','analysis_method','measurement_method','study_design'),1):
        p=paragraphs[(i-1)%2]
        records.append(dict(knowledge_id=f'knowledge_{i:04d}',knowledge_type=typ,
            **{k:[] for k in mk.LISTS},population={k:None for k in ('description','sample_size','age','setting')},
            provenance=[{k:p[k] for k in ('paragraph_id','section_id','section_title','source_text_sha256')}],
            verification_status='pending',extraction=dict(status='machine_extracted',human_verified=False)))
        records[-1]['target_concepts']=['quadruped','CPG']
        records[-1]['experimental_methods']=['CPG tuning']
        records[-1]['analysis_methods']=['Statistical analysis']
    mk.jsonl(root/'machine_knowledge.jsonl',records)
    return root


@pytest.fixture
def output_root(tmp_path,monkeypatch):
    root=tmp_path/'phase5s'
    monkeypatch.setattr(e,'DEFAULT_OUTPUT_ROOT',root)
    return root


def data(request):
    return json.loads(request['prompt'].split('INPUT_DATA_JSON:\n')[1])


class FakeProvider:
    def __init__(self,fail_at=None,invalid_at=None):
        self.requests=[]
        self.fail_at=fail_at
        self.invalid_at=invalid_at

    def generate(self,request,decoding):
        assert decoding==e.DECODING
        self.requests.append(copy.deepcopy(request))
        if len(self.requests)==self.fail_at:
            raise RuntimeError('failure; do not persist provider error body')
        if len(self.requests)==self.invalid_at:
            return LLMResponse('{}')
        value=data(request)
        if request['output_schema']==e.router.TASK_SCHEMA:
            answer=e.fixture_task(value['user_request'])
        else:
            answer=e.fixture_answer(value.get('context'))
        return LLMResponse(json.dumps(answer,ensure_ascii=False),{'total_tokens':7})


def source_row(sid='paragraph_1',text='CPG quadruped analysis'):
    return dict(source_id=sid,category='source_paragraph',domain='quadruped_cpg',
                title='methods',text=text,paragraph_id=sid,source_text_sha256=e.digest(text.encode()))


def test_gold_is_pinned_and_public_projection_has_no_gold(tmp_path):
    gold=e.load_gold()
    assert len(gold['cases'])==10
    before=copy.deepcopy(gold)
    for row in gold['cases']: row['gold']['secret']='GOLD_SENTINEL'
    public=e.public_cases(gold)
    assert all(set(r)=={'sample_id','request'} for r in public)
    assert 'GOLD_SENTINEL' not in e.canonical(public)
    bad=tmp_path/'changed_gold.json'
    bad.write_text(json.dumps(before)+' ')
    with pytest.raises(ValueError,match='Gold hash mismatch'): e.load_gold(bad)


def test_requests_exactly_match_fixed_protocol():
    rows=e.public_cases(e.load_gold())
    assert rows[0]['request']=='四足ロボットのCPG制御研究で、CPGパラメータをどのような手順で調整したか説明してください。'
    assert rows[-1]['request']=='明日のドル円相場を具体的な数値で予測してください。'
    assert all('sensor_history' not in r['gold']['required_information'] for r in e.load_gold()['cases'])


def test_b_fixed_bm25_no_routing_and_no_input_mutation(monkeypatch):
    def forbidden(*a,**k): raise AssertionError('B must not invoke Router/connectors')
    monkeypatch.setattr(e.production,'bounded_context',forbidden)
    monkeypatch.setattr(e.router,'make_request',forbidden)
    docs=[source_row('p2'),source_row('p1'),source_row('other','unrelated finance')]
    before=copy.deepcopy(docs)
    retriever=e.SimpleRAG(docs)
    first=e.simple_context('CPG四足の解析',retriever)
    assert first==e.simple_context('CPG四足の解析',e.SimpleRAG(list(reversed(docs))))
    assert [r['source_id'] for r in first[0]['evidence']]==['p1','p2']
    assert all(r['retrieval_score']>0 for r in first[0]['evidence'])
    assert 'routing' not in first[0] and 'task_spec' not in e.canonical(first)
    assert docs==before
    assert e.SEARCH_PROTOCOL['k1']==1.2 and e.SEARCH_PROTOCOL['b']==0.75
    assert len(e.SimpleRAG([source_row(f'p{i}') for i in range(12)]).retrieve('CPG'))==8


def test_b_has_no_domain_guard():
    result=e.SimpleRAG([source_row()]).retrieve('Parkinson quadruped CPG')
    assert result  # Relevance is assessed later, never a medical-domain guard here.


def test_bm25_score_matches_fixed_formula():
    docs=[source_row('a','alpha alpha'),source_row('b','beta')]
    for r in docs: r['title']=''
    ranked=e.SimpleRAG(docs).retrieve('alpha alpha')
    import math
    expected=math.log(1+(2-1+0.5)/(1+0.5))*2*2.2/(2+1.2*(0.25+0.75*2/1.5))
    assert ranked[0]['retrieval_score']==pytest.approx(expected)


def test_c_has_request_only_and_no_context():
    request=e.answer_request('request',reference_only=True)
    assert data(request)=={'request':'request'}
    assert request['output_schema']==e.ANSWER_SCHEMA
    with pytest.raises(ValueError,match='request only'):
        e.answer_request('request',{'evidence':[]},True)


def test_ab_instructions_and_budget_identical():
    context_a,a=e.bound_context([source_row('a','x'*3000)],routing={'task_spec':'x'},limit=1000)
    context_b,b=e.bound_context([source_row('b','x'*3000)],limit=1000)
    for context,budget in [(context_a,a),(context_b,b)]:
        assert e.context_size(context)<=1000
        assert budget['method']==e.BUDGET_METHOD and budget['limit']==1000
        assert budget['dropped_source_ids']
        assert 'pipeline_limitation' in context['insufficiency_reasons']
    r1=e.answer_request('same',context_a)
    r2=e.answer_request('same',context_b)
    assert r1['prompt'].split('INPUT_DATA_JSON:')[0]==r2['prompt'].split('INPUT_DATA_JSON:')[0]
    assert r1['output_schema']==r2['output_schema']


def test_common_schema_and_reason_required():
    answer=e.fixture_answer()
    e.validate_answer(answer)
    answer['insufficient_information']=[]
    with pytest.raises(ValueError,match='reason'): e.validate_answer(answer)
    answer=e.fixture_answer()
    answer['insufficient_information'][0]['reason']='uncontrolled_reason'
    with pytest.raises(ValueError): e.validate_answer(answer)
    assert set(e.REASONS)=={'no_relevant_knowledge','connector_unavailable','domain_mismatch','retrieval_failure','pipeline_limitation','out_of_scope'}


def test_existing_proposed_routing_guard_and_unavailable(source):
    adapter=e.MachineKnowledgeAdapter(source)
    task=e.production.fixture_task('C')
    context,budget,raw=e.proposed_context('パーキンソン病の研究センサー',task,adapter)
    assert not context['evidence']
    assert {'connector_unavailable','domain_mismatch','no_relevant_knowledge'}<=set(context['insufficiency_reasons'])
    assert {r['connector'] for r in raw['connector_results']}=={'ScholarlyEvidenceConnector','ExperimentalMethodConnector','SensorCapabilityConnector'}
    context,_,_=e.proposed_context('四足CPG実験',e.production.fixture_task('A'),adapter)
    assert context['evidence'] and all(r['category']=='machine_extracted_knowledge' for r in context['evidence'])


def test_run_retains_failures_and_same_request_no_gold(source,output_root):
    provider=FakeProvider(fail_at=1,invalid_at=3)
    output=output_root/'mock'
    manifest=e.run(output,source,mode='mock',provider=provider)
    rows=json.loads((output/'results.json').read_text())
    assert len(rows)==30 and manifest['status']=='completed_with_failures'
    assert rows[0]['status']=='failed' and rows[0]['failed_stage']=='routing'
    assert rows[0]['insufficiency_reasons']==['pipeline_limitation']
    assert len(provider.requests)<=40
    for case in e.public_cases(e.load_gold()):
        matches=[r for r in rows if r['sample_id']==case['sample_id']]
        assert [r['condition'] for r in matches]==['A','B','C']
        assert all(r['request']==case['request'] for r in matches)
    for r in rows:
        for req in r['requests']:
            payload=data(req['request'])
            assert 'gold' not in payload and 'sample_id' not in payload
            assert not any(k in e.canonical(payload) for k in ('allowed_task_types','required_evidence_groups','allowed_answer_states'))
            if r['condition']=='C': assert set(payload)=={'request'}
            if r['condition']=='B': assert 'routing' not in payload['context']
    assert all(r['insufficiency_reasons'] for r in rows if r['status']=='failed')
    with pytest.raises(ValueError,match='overwrite'): e.run(output,source)


def test_retrieval_failure_preserved(source,output_root,monkeypatch):
    monkeypatch.setattr(e.SimpleRAG,'retrieve',lambda *a,**k: (_ for _ in ()).throw(ValueError('bad index')))
    e.run(output_root/'failed',source)
    rows=json.loads((output_root/'failed/results.json').read_text())
    b=[r for r in rows if r['condition']=='B']
    assert len(b)==10 and all(r['status']=='failed' and 'retrieval_failure' in r['insufficiency_reasons'] for r in b)


def test_dry_run_zero_api_and_reject_unsafe_output(source,output_root,monkeypatch):
    def forbidden(*a,**k): raise AssertionError('No live calls permitted')
    monkeypatch.setattr(e.OpenAIProvider,'generate',forbidden)
    result=e.run(output_root/'dry',source)
    assert result['generation_attempts']==0 and result['result_count']==30
    assert result['fixture_notice']
    with pytest.raises(ValueError,match='Outputs must'): e.check_output(Path('/tmp/not-authorized-output'))
    with pytest.raises(ValueError,match='identical corpus'): e.run(output_root/'other',source,corpus_dir=output_root)
    with pytest.raises(ValueError,match='validation'): e.run(output_root/'live',source,mode='live')
    assert not (output_root/'live').exists()


def test_gold_mutation_detected_before_next_call(source,output_root,monkeypatch,tmp_path):
    frozen=tmp_path/'gold.json'
    frozen.write_bytes(e.DEFAULT_GOLD.read_bytes())
    monkeypatch.setattr(e,'DEFAULT_GOLD',frozen)
    class Mutating(FakeProvider):
        def generate(self,request,decoding):
            response=super().generate(request,decoding)
            frozen.write_text(frozen.read_text()+' ')
            return response
    provider=Mutating()
    with pytest.raises(e.IntegrityViolation,match='Gold/source changed'):
        e.run(output_root/'mutated',source,mode='mock',provider=provider)
    assert len(provider.requests)==1
    rows=json.loads((output_root/'mutated/results.json').read_text())
    assert len(rows)==1 and rows[0]['status']=='integrity_failure'
    assert not (output_root/'mutated/deterministic_metrics.json').exists()
    state=json.loads((output_root/'mutated/manifest.json').read_text())
    assert state['status']=='integrity_failure' and state['generation_attempts']==1


def test_source_and_protected_files_unchanged(source,output_root):
    repo=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip())
    protected=[repo/'ros4hc_observation/ros4hc_synthetic_scenario/PHASE5J.md',
        repo/'ros4hc_observation/ros4hc_synthetic_scenario/ros4hc_synthetic_scenario/evidence_comparison_experiment.py',
        repo/'ros4hc_observation/ros4hc_synthetic_scenario/test/test_evidence_comparison_experiment.py']
    protected += [p for p in (repo/'ros4hc_lib/apple_watch_replay').rglob('*') if p.is_file()]
    protected += list(source.iterdir())
    before={p:p.read_bytes() for p in protected if p.is_file()}
    e.run(output_root/'protected',source)
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_blind_audit_separate_no_gold_no_arm_and_queue(source,output_root):
    run_dir=output_root/'run'
    e.run(run_dir,source)
    rows=json.loads((run_dir/'results.json').read_text())
    frozen=e.FrozenRun(run_dir)
    packets,mapping=e.blind_packets(frozen)
    assert len(packets)==len(mapping)==30
    for p in packets:
        payload=data(e.audit_request(p,frozen))
        assert set(payload)=={'request','answer','context','reference_only','rubric'}
        assert not {'condition','sample_id','gold'} & set(payload)
        assert payload['context'] is None or 'routing' not in payload['context']
    audit_dir=output_root/'audit'
    manifest=e.run_audit(run_dir,audit_dir)
    assert manifest['actual_api_calls']==0 and manifest['audited']==30
    queue=json.loads((audit_dir/'spot_check_candidates.json').read_text())
    assert all(r['human_review_status']=='not_started' and r['notice']==e.NOTICE for r in queue)
    assert all(r['notice']==e.NOTICE for r in json.loads((audit_dir/'case_comparison.json').read_text()))


def test_audit_failure_kept_and_tampered_results_rejected(source,output_root):
    run_dir=output_root/'run'
    e.run(run_dir,source)
    result=e.run_audit(run_dir,output_root/'bad_audit',mode='mock',provider=FakeProvider())
    assert result['status']=='completed_with_failures'
    assert result['audited']==30 and result['actual_api_calls']==30
    results=run_dir/'results.json'
    results.write_text(results.read_text()+' ')
    with pytest.raises(ValueError,match='Changed results'): e.run_audit(run_dir,output_root/'tampered')


def test_groundedness_validation_and_spot_queue_triggers():
    base=dict(claims=[dict(statement_ja='claim',label='Partial',source_ids=[],reason_ja='qualification')],
              evidence=[],insufficiency='inappropriate',reason_ja='missing',domain_mismatch='yes',over_rejection='yes',uncertainties=['math'],
              partial_answer_handling='over_rejection',partial_answer_reason_ja='missing',
              judge_confidence='low',confidence_reason_ja='uncertain')
    row=dict(sample_id='S01',condition='A',status='success',context={'evidence':[]},insufficiency_reasons=[],supplementary_validation={'valid':True})
    audits={'R01':dict(status='success',audit=base)}
    queue=e.spot_check_candidates([row],audits,{'R01':dict(sample_id='S01',condition='A')})
    assert {'Partial','inappropriate_insufficiency','domain_mismatch','over_rejection','audit_uncertainty'}<=set(queue[0]['reasons'])
    packet={'data':{'request':'q','answer':{'answer_ja':'a'},'context':{'evidence':[]},'reference_only':False}}
    e.validate_audit(base,packet)
    base['claims'][0]['label']=None
    with pytest.raises(ValueError,match='N/A'): e.validate_audit(base,packet)


def test_citation_membership_hash_and_c_closed_book():
    row=source_row()
    context,_=e.bound_context([row])
    answer=e.fixture_answer()
    answer['citations']=[dict(statement_ja='q',source_id=row['source_id'],paragraph_id=row['paragraph_id'])]
    assert e.citation_validation(answer,context)['valid']
    context['evidence'][0]['text']='tampered'
    assert not e.citation_validation(answer,context)['valid']
    with pytest.raises(ValueError,match='fabricated'): e.validate_answer(answer,reference_only=True)


def test_b_corpus_projection_ignores_structured_record_changes(source):
    adapter=e.MachineKnowledgeAdapter(source)
    before=e.source_documents(adapter,[])
    adapter.records[0]['experimental_methods']=['GOLD_OR_ROUTER_SENTINEL']
    assert e.source_documents(adapter,[])==before
    assert 'GOLD_OR_ROUTER_SENTINEL' not in e.canonical(before)


def test_disagreement_candidates_and_c_na_are_not_false_disagreement():
    good=dict(claims=[dict(statement_ja='s',label='Supported',source_ids=[],reason_ja='r')],
        evidence=[],insufficiency='appropriate',reason_ja='r',domain_mismatch='no',over_rejection='no',uncertainties=[],
        partial_answer_handling='not_applicable',partial_answer_reason_ja='not partial',
        judge_confidence='high',confidence_reason_ja='clear')
    rows=[dict(sample_id='S01',condition=arm,status='success',answer=dict(answer_state='answerable'),context=None,insufficiency_reasons=[],supplementary_validation={'valid':True}) for arm in ('A','B','C')]
    audits={f'R{i}':dict(status='success',audit=copy.deepcopy(good)) for i in range(3)}
    audits['R2']['audit']['claims'][0]['label']=None
    mapping={f'R{i}':dict(sample_id='S01',condition=arm) for i,arm in enumerate(('A','B','C'))}
    assert e.spot_check_candidates(rows,audits,mapping)==[]
    audits['R1']['audit']['claims'][0]['label']='Unsupported'
    reasons=e.spot_check_candidates(rows,audits,mapping)[0]['reasons']
    assert 'A_B_disagreement' in reasons and 'Unsupported' in reasons


def audit_value(**overrides):
    value=dict(claims=[dict(statement_ja='claim',label='Supported',source_ids=[],reason_ja='context')],
        evidence=[],insufficiency='appropriate',reason_ja='rubric checked',domain_mismatch='no',over_rejection='no',
        partial_answer_handling='not_applicable',partial_answer_reason_ja='not partial',
        judge_confidence='high',confidence_reason_ja='clear context',uncertainties=[])
    value.update(overrides)
    return value


def test_generation_never_unlocks_or_receives_rubric(source,output_root,monkeypatch):
    run_dir=output_root/'generation'
    class Inspecting(FakeProvider):
        def generate(self,request,decoding):
            payload=data(request)
            e.reject_evaluation_data(payload)
            assert not any(k in e.canonical(payload) for k in e.RUBRIC_SCHEMA['properties'])
            assert 'rubric' not in payload and 'gold' not in payload
            with pytest.raises(e.IntegrityViolation): e.FrozenRun(run_dir)
            return super().generate(request,decoding)
    provider=Inspecting()
    result=e.run(run_dir,source,mode='mock',provider=provider)
    assert len(provider.requests)==40 and result['results_frozen'] is True
    assert e.FrozenRun(run_dir).verify()['results_sha256']==result['results_sha256']


@pytest.mark.parametrize('key',list(e.RUBRIC_SCHEMA['properties'])+['gold','rubric','expected_connectors','required_evidence_groups'])
def test_generation_context_rejects_evaluation_data_before_freeze(key):
    context={'evidence':[], 'routing':{'nested':{key:[]}}}
    with pytest.raises(ValueError,match='Evaluation data'): e.answer_request('request',context)


def test_judge_requires_complete_freeze_and_minimal_rubric(source,output_root):
    run_dir=output_root/'frozen'
    e.run(run_dir,source)
    with pytest.raises(ValueError,match='FrozenRun'): e.blind_packets([])
    with pytest.raises(ValueError,match='frozen'): e.judge_rubric(None,'S07')
    frozen=e.FrozenRun(run_dir)
    packets,mapping=e.blind_packets(frozen)
    gold={r['sample_id']:r['gold'] for r in e.load_gold()['cases']}
    for packet in packets:
        payload=data(e.audit_request(packet,frozen))
        assert set(payload)=={'request','answer','context','reference_only','rubric'}
        assert set(payload['rubric'])==set(e.RUBRIC_SCHEMA['properties'])
        sid=mapping[packet['blind_id']]['sample_id']
        assert payload['rubric']['partial_answer_expected']==(sid in ('S07','S08','S09'))
        assert not {'condition','sample_id','gold','routing','task_spec'} & set(payload)
        assert payload['context'] is None or 'routing' not in payload['context']
        # Actual retrieved IDs remain in context/citations; no reference/expected IDs in rubric.
        rubric_text=e.canonical(payload['rubric'])
        assert not any(k in rubric_text for k in ('paragraph_', 'knowledge_', 'fall:paper_', 'Connector',
                                                  'expected_domains','expected_connectors','required_evidence_groups'))
        assert payload['rubric']['required_outputs']==gold[sid]['required_outputs']
        for arm in ('A','B','C'): assert arm not in payload.values()
    packet=copy.deepcopy(packets[0]); packet['data']['rubric']['required_outputs']=['changed']
    with pytest.raises(ValueError,match='differs'): e.audit_request(packet,frozen)
    manifest_path=run_dir/'manifest.json'; manifest=json.loads(manifest_path.read_text())
    manifest['results_frozen']=False; manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(e.IntegrityViolation): e.audit_request(packets[0],frozen)
    with pytest.raises(e.IntegrityViolation): e.FrozenRun(run_dir)


@pytest.mark.parametrize('sid',['S07','S08','S09'])
@pytest.mark.parametrize('state',['partial','insufficient','reject'])
def test_partial_refusal_or_missing_supported_outputs_always_queued(sid,state):
    row=dict(sample_id=sid,condition='B',status='success',context={'evidence':[source_row()]},
        answer=dict(answer_ja='回答できません。',answer_state=state,citations=[]),
        insufficiency_reasons=[],supplementary_validation={'valid':True})
    queue=e.spot_check_candidates([row],{}, {}, include_audit=False)
    reasons=set(queue[0]['reasons'])
    assert {'expected_partial_empty_body','supported_required_outputs_missing_candidate',
            'partial_required_outputs_semantic_review'}<=reasons
    if state!='partial':
        assert {'expected_answer_state_mismatch','expected_partial_full_refusal_candidate',
                'evidence_present_full_refusal_candidate'}<=reasons
    assert queue[0]['human_review_status']=='not_started'


def test_partial_missing_outputs_with_citations_still_requires_semantic_review():
    row=dict(sample_id='S09',condition='A',status='success',context={'evidence':[source_row()]},
        answer=dict(answer_ja='根拠はありますが、具体的な実験手順や解析方法の説明は省略します。',answer_state='partial',
                    citations=[dict(source_id='paragraph_1',paragraph_id='paragraph_1',statement_ja='根拠あり')]),
        insufficiency_reasons=[],supplementary_validation={'valid':True})
    queue=e.spot_check_candidates([row],{}, {}, include_audit=False)
    assert 'partial_required_outputs_semantic_review' in queue[0]['reasons']


@pytest.mark.parametrize('change,expected',[
    ({'claims':[dict(statement_ja='s',label='Partial',source_ids=[],reason_ja='r')]},'Partial'),
    ({'claims':[dict(statement_ja='s',label='Unsupported',source_ids=[],reason_ja='r')]},'Unsupported'),
    ({'evidence':[dict(source_id='p',label='partly_relevant',reason_ja='r')]},'partly_relevant_evidence'),
    ({'evidence':[dict(source_id='p',label='irrelevant',reason_ja='r')]},'irrelevant_evidence'),
    ({'insufficiency':'partly_appropriate'},'partly_appropriate_insufficiency'),
    ({'insufficiency':'inappropriate'},'inappropriate_insufficiency'),
    ({'partial_answer_handling':'over_rejection'},'over_rejection'),
    ({'domain_mismatch':'yes'},'domain_mismatch'),
    ({'uncertainties':['table/formula']},'audit_uncertainty'),
    ({'judge_confidence':'low'},'low_judge_confidence'),
])
def test_all_judge_review_triggers(change,expected):
    row=dict(sample_id='S01',condition='A',status='success',context=None,
        answer={'answer_state':'answerable'},insufficiency_reasons=[],supplementary_validation={'valid':True})
    audit=audit_value(**change)
    queue=e.spot_check_candidates([row],{'R':{'status':'success','audit':audit}}, {'R':{'sample_id':'S01','condition':'A'}})
    assert expected in queue[0]['reasons']


def test_top_k_and_byte_exclusions_are_disjoint():
    rows=[source_row(f'p{i}', 'x'*5000 if i==0 else 'CPG') for i in range(10)]
    context,budget=e.bound_context(rows,limit=1200)
    assert budget['candidate_evidence_count']==10
    assert budget['dropped_by_top_k']==['p8','p9']
    assert 'p0' in budget['dropped_by_byte_budget']
    assert not set(budget['dropped_by_top_k']) & set(budget['dropped_by_byte_budget'])
    assert budget['selected_evidence_count']==len(context['evidence'])
    assert budget['selected_context_bytes']==e.context_size(context)<=1200
    assert len(budget['candidate_item_bytes'])==10
    assert budget['context_item_bytes']==[dict(source_id=r['source_id'],bytes=e.context_size(r)) for r in context['evidence']]
    assert budget['candidate_evidence_count']==budget['selected_evidence_count']+len(budget['dropped_source_ids'])


def test_b_candidate_logging_does_not_change_ranking():
    retriever=e.SimpleRAG([source_row(f'p{i}') for i in range(12)])
    ranked=retriever.retrieve('CPG')
    context,budget=e.simple_context('CPG',retriever)
    assert context['evidence']==ranked
    assert budget['candidate_evidence_count']==12 and len(budget['dropped_by_top_k'])==4


def test_ab_context_counts_and_bytes_saved(source,output_root):
    run_dir=output_root/'bytes'
    e.run(run_dir,source)
    rows=json.loads((run_dir/'results.json').read_text())
    for row in rows:
        if row['condition']=='C':
            assert row['context'] is None and row['budget'] is None
            continue
        budget=row['budget']; context=row['context']
        assert budget['selected_context_bytes']==len(e.canonical(context).encode('utf-8'))
        assert budget['selected_evidence_count']==len(context['evidence'])<=8
        assert budget['candidate_evidence_count']==budget['selected_evidence_count']+len(budget['dropped_by_top_k'])+len(budget['dropped_by_byte_budget'])
        assert sum(r['bytes'] for r in budget['context_item_bytes'])<=budget['selected_context_bytes']<=16000
        if row['condition']=='A': assert 'task_spec' in context['routing']
    metrics=json.loads((run_dir/'deterministic_metrics.json').read_text())
    assert all(m['partial_answer_expected'] for m in metrics if m['sample_id'] in ('S07','S08','S09'))
    queue=json.loads((run_dir/'deterministic_spot_check_candidates.json').read_text())
    assert {'S07','S08','S09'}<={r['sample_id'] for r in queue}


def test_a_logs_candidates_before_production_global_top_k(source):
    adapter=e.MachineKnowledgeAdapter(source)
    original=adapter.fetch
    def expanded(*args,**kwargs):
        result=original(*args,**kwargs)
        if result['status']=='available':
            rows=[]
            for i in range(8):
                row=copy.deepcopy(result['data']['knowledge'][0])
                row['knowledge_id']=f"knowledge_{result['connector']}_{i}"
                rows.append(row)
            result['data']['knowledge']=rows
        return result
    adapter.fetch=expanded
    task=e.production.fixture_task('A')
    task['required_information'].append('scholarly_evidence')
    task['requires_scholarly_evidence']=True
    context,budget,raw=e.proposed_context('四足CPG実験',task,adapter)
    assert budget['candidate_evidence_count']==16
    assert len(budget['dropped_by_top_k'])==8
    assert budget['candidate_evidence_count']==len(context['evidence'])+len(budget['dropped_by_top_k'])+len(budget['dropped_by_byte_budget'])


def test_new_audit_labels_and_closed_book_na_validation():
    packet={'data':{'request':'q','answer':{'answer_ja':'a'},'context':{'evidence':[source_row()]},'reference_only':False}}
    value=audit_value(evidence=[dict(source_id='paragraph_1',label='partly_relevant',reason_ja='part only')],
        insufficiency='partly_appropriate',partial_answer_handling='partial_supported_part',partial_answer_reason_ja='missing method')
    assert e.validate_audit(value,packet)==value
    packet['data'].update(reference_only=True,context=None)
    value=audit_value(claims=[dict(statement_ja='s',label=None,source_ids=[],reason_ja='reference only')])
    e.validate_audit(value,packet)
    value['partial_answer_handling']='over_rejection'
    with pytest.raises(ValueError,match='N/A'): e.validate_audit(value,packet)



def test_ab_label_distribution_disagreement_is_not_hidden_by_same_worst_label():
    good=audit_value()
    different=copy.deepcopy(good)
    different['claims'].append(dict(statement_ja='second',label='Supported',source_ids=[],reason_ja='r'))
    rows=[dict(sample_id='S01',condition=arm,status='success',answer={'answer_state':'answerable'},
        context=None,insufficiency_reasons=[],supplementary_validation={'valid':True}) for arm in ('A','B')]
    audits={'R1':dict(status='success',audit=good),'R2':dict(status='success',audit=different)}
    mapping={'R1':dict(sample_id='S01',condition='A'),'R2':dict(sample_id='S01',condition='B')}
    assert 'A_B_disagreement' in e.spot_check_candidates(rows,audits,mapping)[0]['reasons']


def test_startup_gold_mismatch_stops_before_any_call(source,output_root,tmp_path,monkeypatch):
    gold=tmp_path/'bad-gold.json'; gold.write_bytes(e.DEFAULT_GOLD.read_bytes()+b' ')
    monkeypatch.setattr(e,'DEFAULT_GOLD',gold)
    provider=FakeProvider()
    with pytest.raises(e.IntegrityViolation): e.run(output_root/'bad',source,mode='mock',provider=provider)
    assert not provider.requests and not (output_root/'bad').exists()


def test_partial_assessment_cannot_be_na():
    packet={'data':{'request':'q','answer':{'answer_ja':'a'},'context':None,'reference_only':False,
                    'rubric':{'partial_answer_expected':True}}}
    with pytest.raises(ValueError,match='cannot be N/A'): e.validate_audit(audit_value(),packet)


def test_mock_judge_roundtrip_and_rubric_never_used_as_grounding(source,output_root):
    run_dir=output_root/'roundtrip'
    e.run(run_dir,source)
    class Judge:
        def generate(self,request,decoding):
            payload=data(request)
            assert request['output_schema']==e.AUDIT_SCHEMA
            assert not {'condition','sample_id','gold'} & set(payload)
            partial=payload['rubric']['partial_answer_expected'] and not payload['reference_only']
            value=audit_value(claims=[dict(statement_ja='fixture',label=None if payload['reference_only'] else 'Supported',
                source_ids=[],reason_ja='fixture only')],
                evidence=[dict(source_id=r['source_id'],label='relevant',reason_ja='fixture only') for r in (payload['context'] or {}).get('evidence',[])],
                partial_answer_handling='partial_supported_part' if partial else 'not_applicable')
            return LLMResponse(json.dumps(value),{'total_tokens':1})
    manifest=e.run_audit(run_dir,output_root/'judge',mode='mock',provider=Judge())
    assert manifest['status']=='completed' and manifest['actual_api_calls']==30
    assert 'Groundednessは実際のcontextのみ' in e.AUDIT_INSTRUCTIONS

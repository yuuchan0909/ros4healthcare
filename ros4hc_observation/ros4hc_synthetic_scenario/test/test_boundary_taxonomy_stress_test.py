import copy
import json
import pytest
from ros4hc_synthetic_scenario import boundary_taxonomy_stress_test as m


def dataset():
    questions=[dict(**q,user_request='研究相談 '+q['question_id']) for q in m.plan()]
    items=[dict(question_id=q['question_id'],user_request=q['user_request'],
                task_spec=m.router.validate_task(m.router.fixture_task('B'))) for q in questions]
    return questions,items,m.fixture_audits(items)


def test_plan_and_independence():
    qs,_,_=dataset()
    assert len({q['question_id'] for q in qs})==48
    assert sorted(m.Counter(q['boundary_theme'] for q in qs).values())==[4,4]+[5]*8
    assert m.independence_audit(m.generator_prompts())['candidate_name_absent']
    for leak in ('inference_appraisal',m.router.TASK_TYPES[0],m.router.INFORMATION[0]):
        with pytest.raises(ValueError):
            m.independence_audit(m.generator_prompts()+[leak])


@pytest.mark.parametrize('kind',['id','duplicate','theme','batch'])
def test_question_rejection(kind):
    qs,_,_=dataset()
    key={'id':'question_id','duplicate':'user_request','theme':'boundary_theme','batch':'generation_batch'}[kind]
    qs[12][key]=qs[0][key] if kind!='theme' else 'Z'
    with pytest.raises(Exception):
        m.validate_questions(qs)


@pytest.mark.parametrize('field,value',[
    ('mapping_status','unsupported_capability'),('mapping_status','true_unknown'),
    ('multi_intent_preservation','major_intent_lost'),('extra',False),
    ('temporal_intent_preservation','lost'),('candidate_new_task',['new_task'])])
def test_audit_rejection(field,value):
    _,items,aa=dataset()
    aa[0][field]=value
    with pytest.raises(Exception):
        m.validate_audits(aa,items)


def test_aggregation():
    assert m.canonical('research_inference_evaluation') == 'inference_appraisal'
    qs,items,aa=dataset()
    for i,a in enumerate(aa):
        a.update(mapping_status='forced_mapping',natural_mapping=False,intent_preserved=False,
                 candidate_new_task=['inference_validity_assessment' if i%2 else 'critical_inference_appraisal'],
                 candidate_new_information=['data_quality'],limitation_source='taxonomy',
                 multi_intent_preservation='partially_represented',
                 temporal_intent_preservation='partially_preserved',missing_intent_dimensions=['baseline'])
    result=m.aggregate(qs,items,aa)
    assert result==m.aggregate(qs,items,list(reversed(aa)))
    stats,tasks,info,multi,temporal,queue=result
    assert tasks[0]['canonical_candidate']=='inference_appraisal' and tasks[0]['count']==48
    assert tasks[0]['strength']=='strong' and len(tasks[0]['boundary_themes'])==10
    assert info[0]['count']==48 and multi['counts']=={'partially_represented':48}
    assert len(temporal['issues'])==48 and len(queue)==20
    assert all(q[k]=='' for q in queue for k in (
        'human_mapping_status','human_preferred_task','human_missing_information','human_note'))
    task=items[0]['task_spec']['task_type']
    assert stats['task_overload'][task]['assigned_count']==48
    assert not stats['task_overload'][task]['catch_all_risk']
    aa[0]['candidate_new_task']=['reproducibility_assessment']
    assert m.aggregate(qs,items,aa)[0]['task_overload'][task]['catch_all_risk']
    assert [m.strength(n) for n in (1,2,3,4)]==['weak','emerging','emerging','strong']


def test_auditor_payload():
    _,items,_=dataset()
    req=m.audit_request(items[:12])
    assert 'unsupported_capability' not in req['prompt']
    assert 'Connector不足' not in req['prompt']
    data=json.loads(req['prompt'].split('\nITEMS:\n')[1])
    assert all(set(i)=={'question_id','user_request','task_spec'} for i in data)
    payload=m.OpenAIProvider._payload('gpt-6-astra',req,{'reasoning_effort':'medium'})
    assert payload['store'] is False
    assert not {'tools','temperature','web'} & set(payload)


def test_fixture_no_api_or_context_and_key_safety(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','sentinel-credential-never-persist')
    def forbidden(*args,**kwargs):
        raise AssertionError('No API/context')
    monkeypatch.setattr(m.OpenAIProvider,'generate',forbidden)
    monkeypatch.setattr(m.blind,'build_context',forbidden)
    manifest=m.run(tmp_path/'fixture',True)
    assert manifest['actual_api_calls']=={} and manifest['status']=='fixture_complete'
    files=list((tmp_path/'fixture').iterdir())
    assert len(files)==13
    assert all('sentinel-credential-never-persist' not in p.read_text() for p in files)
    assert json.loads((tmp_path/'fixture'/'final_audit.json').read_text())['passed']


def test_live_orchestration_with_mock_provider(tmp_path,monkeypatch):
    qs,items,aa=dataset()
    calls=[]
    class Provider:
        def __init__(self,*args,**kwargs):
            pass
        def generate(self,req,decoding):
            calls.append(req)
            props=req['output_schema']['properties']
            if 'questions' in props:
                n=sum('questions' in c['output_schema']['properties'] for c in calls)-1
                value={'questions':qs[n*12:(n+1)*12]}
            elif 'audits' in props:
                n=sum('audits' in c['output_schema']['properties'] for c in calls)-1
                value={'audits':aa[n*12:(n+1)*12]}
            else:
                value=items[0]['task_spec']
            return m_response(json.dumps(value))
    from ros4hc_synthetic_scenario.llm_providers import LLMResponse as m_response
    monkeypatch.setattr(m,'OpenAIProvider',Provider)
    result=m.run(tmp_path/'mock',False)
    assert result['actual_api_calls']=={'generator':4,'router':48,'auditor':4}
    assert len(calls)==56

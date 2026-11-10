import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario import mapping_only_audit as m

SOURCE = Path('/home/bushi/ros4hc_experiments/phase5o/taxonomy_stress_test_20261108')


def inputs(tmp_path):
    questions = [dict(question_id=f'Q{i:03d}', user_request=f'研究相談{i}') for i in range(1,49)]
    routed = [dict(**q, status='success', task_spec=m.router.fixture_task('B'),
                   context={'old_audit': 'unsupported_capability'}) for q in questions]
    for name, rows in [('generated_questions', questions), ('router_results', routed)]:
        m.save_jsonl(tmp_path/(name+'.jsonl'), rows)
    return tmp_path


def test_actual_inputs():
    if not SOURCE.exists():
        pytest.skip('External Phase 5O artifacts not installed')
    items, audit = m.load_inputs(SOURCE)
    assert len(items) == 48 and audit['assigned_task_counts'] == {
        'research_planning':35, 'experiment_planning':12, 'additional_measurement_planning':1}


@pytest.mark.parametrize('kind', ['id', 'router_id', 'text', 'router_text', 'duplicate_key'])
def test_input_rejection(tmp_path, kind):
    inputs(tmp_path)
    name = 'router_results' if kind.startswith('router') else 'generated_questions'
    path = tmp_path/(name+'.jsonl')
    rows = m.read_jsonl(path)
    if kind in ('id', 'router_id'):
        rows[1]['question_id'] = rows[0]['question_id']
    elif kind == 'text':
        rows[1]['user_request'] = rows[0]['user_request']
    elif kind == 'router_text':
        rows[1]['user_request'] = 'changed'
    else:
        path.write_text('{"question_id":"Q001","question_id":"Q002"}\n')
        with pytest.raises(ValueError):
            m.load_inputs(tmp_path)
        return
    m.save_jsonl(path, rows)
    with pytest.raises(ValueError):
        m.load_inputs(tmp_path)


def test_prompt_independence_and_payload(tmp_path):
    items, _ = m.load_inputs(inputs(tmp_path))
    request = m.make_request(items[:12])
    data = json.loads(request['prompt'].split('\nITEMS:\n')[1])
    assert all(set(i) == {'question_id','user_request','task_spec'} for i in data)
    assert 'unsupported_capability' not in request['prompt']
    assert 'old_audit' not in request['prompt']
    payload = m.OpenAIProvider._payload('gpt-6-astra',request,{'reasoning_effort':'medium'})
    assert payload['store'] is False and 'tools' not in payload and 'temperature' not in payload
    assert payload['reasoning'] == {'effort':'medium'}


@pytest.mark.parametrize('key,value', [('mapping_status','unsupported_capability'),
    ('extra',True), ('natural_mapping','yes'), ('assigned_task_type','research_planning')])
def test_schema(tmp_path,key,value):
    items,_ = m.load_inputs(inputs(tmp_path))
    rows=m.fixture(items)
    rows[0][key]=value
    with pytest.raises(Exception):
        m.validate_audits(rows,items)


def test_duplicate_audit_ids(tmp_path):
    items,_=m.load_inputs(inputs(tmp_path))
    rows=m.fixture(items)
    rows[1]=copy.deepcopy(rows[0])
    with pytest.raises(ValueError):
        m.validate_audits(rows,items)


def test_deterministic_synonyms_and_queue(tmp_path):
    items,_ = m.load_inputs(inputs(tmp_path))
    rows=m.fixture(items)
    for i,a in enumerate(rows):
        a.update(mapping_status='forced_mapping', natural_mapping=False, intent_preserved=False,
            missing_intent_dimensions=['推論','複合目的'], needs_human_review=True,
            candidate_new_task=['research_inference_appraisal' if i%2 else 'inference_validity_assessment'])
    stats, candidates, queue=m.aggregate(items,rows)
    assert (stats,candidates,queue)==m.aggregate(list(reversed(items)),list(reversed(rows)))
    assert candidates[0]['canonical_candidate']=='inference_appraisal'
    assert candidates[0]['count']==48 and candidates[0]['strength']=='stronger_candidate'
    assert len(queue)==20 and len(stats['questions_with_multiple_missing_intent_dimensions'])==48
    assert all(q[k]=='' for q in queue for k in ('human_mapping_status','human_preferred_task','human_note'))
    rows=m.fixture(items)
    rows[0]['candidate_new_task']=['singleton']
    assert m.aggregate(items,rows)[1][0]['strength']=='weak_candidate'


def test_fixture_key_safety_and_no_calls(tmp_path,monkeypatch):
    source=tmp_path/'source'
    source.mkdir()
    inputs(source)
    monkeypatch.setenv('OPENAI_API_KEY','secret-sentinel-never-save')
    def forbidden(*args,**kwargs):
        raise AssertionError('No API, routing, or context calls permitted')
    monkeypatch.setattr(m.OpenAIProvider,'generate',forbidden)
    monkeypatch.setattr(m.router,'make_request',forbidden)
    result=m.run(source,tmp_path/'out',True)
    assert result['actual_api_calls']==0 and result['status']=='fixture_complete'
    assert len(list((tmp_path/'out').iterdir()))==8
    assert all('secret-sentinel-never-save' not in p.read_text() for p in (tmp_path/'out').iterdir())
    assert json.loads((tmp_path/'out'/'final_audit.json').read_text())['passed']

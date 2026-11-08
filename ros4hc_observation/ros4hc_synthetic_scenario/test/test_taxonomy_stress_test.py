import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario import taxonomy_stress_test as m


def questions():
    return [dict(question_id=f'Q{i:03d}', generation_batch=(i-1)//12+1,
                 user_request=f'研究相談{i}') for i in range(1,49)]


@pytest.mark.parametrize('forbidden', [*m.router.TASK_TYPES,*m.router.INFORMATION,*m.CONNECTOR_NAMES])
def test_independence(forbidden):
    assert m.independence_audit(m.generator_prompts())['passed']
    with pytest.raises(ValueError):
        m.independence_audit([m.GENERATOR+forbidden])


def test_questions():
    assert len(m.validate_questions(questions()))==48
    for field in ('question_id','user_request'):
        rows=questions()
        rows[1][field]=rows[0][field]
        with pytest.raises(ValueError):
            m.validate_questions(rows)


def rows():
    return [dict(**q,status='success',task_spec=m.router.parse_task(json.dumps(m.router.fixture_task('B'))),
                 context={}) for q in questions()]


def audits():
    return [dict(question_id=q['question_id'],mapping_status='forced_mapping',assigned_task_type='sensor_selection',
        natural_mapping=False,intent_preserved=False,reason_ja='検証',missing_task_concept=[],missing_information_type=[],
        candidate_new_task=['quality_check'],candidate_new_information=['quality_metrics'],needs_human_review=True) for q in questions()]


def test_audit_validation():
    assert len(m.validate_audits(audits(),rows()))==48
    for key,value in [('mapping_status','invalid'),('extra',True),('natural_mapping','yes')]:
        aa=audits()
        aa[0][key]=value
        with pytest.raises(Exception):
            m.validate_audits(aa,rows())


def test_aggregation():
    result=m.aggregate(rows(),audits())
    assert result==m.aggregate(rows(),audits())
    stats,tasks,information,queue=result
    assert stats['catch_all_suspicions']==['sensor_selection']
    assert tasks[0]['count']==information[0]['count']==48
    assert len(queue)==20
    assert all(q['human_judgment']==q['reviewer_note']=='' for q in queue)


def test_fixture_and_key_safety(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','secret-sentinel-never-save')
    result=m.run(tmp_path/'fixture',True)
    assert result['status']=='fixture_complete'
    assert not result['actual_api_calls']
    assert all('secret-sentinel-never-save' not in p.read_text() for p in (tmp_path/'fixture').iterdir())
    payload=m.OpenAIProvider._payload('gpt-6-astra',m.router.make_request('研究相談'),{'reasoning_effort':'medium'})
    assert payload['store'] is False and 'tools' not in payload and 'temperature' not in payload

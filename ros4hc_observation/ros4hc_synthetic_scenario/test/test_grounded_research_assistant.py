"""Offline boundaries; fixtures use original XML-derived paragraphs."""
import copy
import json
import xml.etree.ElementTree as ET

import pytest

from ros4hc_synthetic_scenario import machine_knowledge as mk
from ros4hc_synthetic_scenario import grounded_research_assistant as g
from ros4hc_synthetic_scenario.machine_knowledge_connector import MachineKnowledgeAdapter
from ros4hc_synthetic_scenario.connector_registry import build_context, generic_request, route


@pytest.fixture
def source(tmp_path):
    raw = b'<article><body><sec><title>Methods</title><p>Quadruped CPG controller experiments.</p><p>Analysis of CPG quadruped locomotion.</p></sec></body></article>'
    xml = tmp_path/'paper.xml'
    xml.write_bytes(raw)
    _, paragraphs, _ = mk.segment(ET.fromstring(raw))
    mk.save(tmp_path/'source_document.json', dict(doi=mk.DOI, pmcid=mk.PMCID,
        title='CPG quadruped multi-agent control', source_path=str(xml), source_sha256=mk.digest(raw), integrity_verified=True))
    mk.jsonl(tmp_path/'source_segments.jsonl', paragraphs)
    records = []
    for i in range(14):
        p = paragraphs[i % 2]
        r = dict(knowledge_type=['experimental_method', 'analysis_method', 'measurement_method', 'study_design'][i % 4],
            **{k: [] for k in mk.LISTS}, population={k: None for k in ('description', 'sample_size', 'age', 'setting')},
            provenance=[{k: p[k] for k in ('paragraph_id', 'section_id', 'section_title', 'source_text_sha256')}],
            knowledge_id=f'knowledge_{i:04d}', verification_status='pending',
            extraction=dict(status='machine_extracted', human_verified=False))
        r.update(target_concepts=['quadruped', 'CPG'], experimental_methods=['Controller trial'], analysis_methods=['Trajectory analysis'])
        records.append(r)
    mk.jsonl(tmp_path/'machine_knowledge.jsonl', records)
    return tmp_path


def fetch(adapter, case='A', top_k=8):
    task = g.fixture_task(case)
    info = [i for i in task['required_information'] if i in ('experimental_methods', 'analysis_methods', 'measurement_methods')]
    return adapter.fetch(info, generic_request(task), dict(g.CASES)[case], top_k=top_k)


def test_load_status_filter_rank_limit_and_no_mutation(source):
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    a = MachineKnowledgeAdapter(source)
    r = fetch(a)
    assert r == fetch(a)
    assert r['status'] == 'available'
    assert r['human_verified'] is False
    assert r['knowledge_status'] == 'machine_extracted'
    assert r['verification_status'] == 'pending'
    rows = r['data']['knowledge']
    assert len(rows) == 8
    assert {'experimental_method', 'analysis_method'} <= {x['knowledge_type'] for x in rows}
    assert all(x['knowledge_type'] in {'experimental_method', 'study_design', 'analysis_method'} for x in rows)
    assert [x['knowledge_id'] for x in rows] == sorted(x['knowledge_id'] for x in rows)
    assert len(fetch(a, top_k=2)['data']['knowledge']) == 2
    with pytest.raises(ValueError):
        fetch(a, top_k=9)
    rows[0]['experimental_methods'].append('mutated')
    assert r != fetch(a)
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}


@pytest.mark.parametrize('text,matched', [
    (mk.DOI, True), (mk.PMCID, True), ('10.1234/wrong', False), ('PMC12345', False),
    ('research experiment method sensor study', False), ('robot control', False),
    ('cardiovascular study', False), ('Parkinson CPG quadruped', False), ('fall quadruped CPG', False),
    ('四足ロボットのCPG制御', True)])
def test_target(source, text, matched):
    a = MachineKnowledgeAdapter(source)
    assert a.match({'target_concepts': []}, text)['matched'] is matched


def test_parkinson_unavailable_and_fall_separated(source):
    a = MachineKnowledgeAdapter(source)
    c = build_context(dict(g.CASES)['C'], g.fixture_task('C'), machine_adapter=a)
    assert not g.knowledge(c)
    assert all(r['status'] == 'unavailable' for r in c['connector_results'])
    d = build_context(dict(g.CASES)['D'], g.fixture_task('D'), machine_adapter=a)
    old = build_context(dict(g.CASES)['D'], g.fixture_task('D'))
    assert d == old
    assert d['connector_results'][0]['status'] == 'available'
    assert d['connector_results'][0]['source_category'] == 'curated_verified_evidence'
    assert not g.knowledge(d)


@pytest.mark.parametrize('mutation', ['id', 'paragraph', 'relation', 'hash', 'extra', 'status', 'warning'])
def test_final_rejections(source, mutation):
    a = MachineKnowledgeAdapter(source)
    c = g.bounded_context(dict(g.CASES)['A'], g.fixture_task('A'), a)
    v = g.fixture_response(c)
    assert g.validate_response(v, c, a)['valid']
    if mutation == 'id':
        v['supported_points'][0]['knowledge_id'] = 'knowledge_fake'
    elif mutation == 'paragraph':
        v['supported_points'][0]['paragraph_id'] = 'paragraph_fake'
    elif mutation == 'relation':
        v['supported_points'][0]['paragraph_id'] = a.paragraphs[1]['paragraph_id']
    elif mutation == 'hash':
        next(iter(g.knowledge(c).values()))['provenance'][0]['source_text_sha256'] = '0'*64
    elif mutation == 'extra':
        v['supported_points'][0]['extra'] = True
    elif mutation == 'status':
        v['supported_points'][0]['verification_status'] = 'verified'
    else:
        v['answer_ja'] = 'No warning'
    with pytest.raises(ValueError):
        g.validate_response(v, c, a)


@pytest.mark.parametrize('mutation', ['schema', 'status', 'human', 'hash', 'segment', 'pair'])
def test_invalid_source(source, mutation):
    if mutation in ('schema', 'status', 'human'):
        rows = [json.loads(x) for x in (source/'machine_knowledge.jsonl').read_text().splitlines()]
        if mutation == 'schema':
            rows[0]['extra'] = 'bad'
        elif mutation == 'status':
            rows[0]['verification_status'] = 'verified'
        else:
            rows[0]['extraction']['human_verified'] = True
        mk.jsonl(source/'machine_knowledge.jsonl', rows)
    elif mutation == 'hash':
        (source/'paper.xml').write_text('changed')
    else:
        rows = [json.loads(x) for x in (source/'source_segments.jsonl').read_text().splitlines()]
        rows[0]['source_text'] = 'changed'
        if mutation == 'pair':
            rows[0]['source_text_sha256'] = mk.digest(b'changed')
        mk.jsonl(source/'source_segments.jsonl', rows)
    with pytest.raises(ValueError):
        MachineKnowledgeAdapter(source)


def test_input_unchanged_and_payload(source):
    a = MachineKnowledgeAdapter(source)
    task = g.fixture_task('A')
    original = copy.deepcopy(task)
    c = g.bounded_context(dict(g.CASES)['A'], task, a)
    before = copy.deepcopy(c)
    g.validate_response(g.fixture_response(c), c, a)
    assert c == before and task == original
    payload = g.OpenAIProvider._payload('gpt-6-astra', g.final_request(c), {'reasoning_effort': 'medium'})
    assert payload['store'] is False and payload['reasoning']['effort'] == 'medium'
    assert not {'tools', 'temperature', 'api_key'} & payload.keys()
    assert payload['text']['format']['schema']['additionalProperties'] is False
    assert route(task)[0]['connector'] == 'ExperimentalMethodConnector'


def test_key_not_saved(source, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret-credential')
    with pytest.raises(ValueError):
        mk.save(source/'bad.json', {'value': 'test-secret-credential'})
    assert not (source/'bad.json').exists()


def test_live_gate_rejects_before_network(source, tmp_path):
    with pytest.raises(ValueError, match='Offline validation'):
        g.run(tmp_path/'output', source, validation=None)


def test_dry_run_zero_network(source, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(g.OpenAIProvider, 'generate', forbidden)
    output = tmp_path/'dry'
    result = g.run(output, source, dry_run=True)
    assert result['network_calls'] == result['actual_api_calls'] == 0
    assert result['status'] == 'dry_run_complete'
    assert len(json.loads((output/'citation_validation.json').read_text())) == 4


def test_post_load_source_change_rejected(source):
    a = MachineKnowledgeAdapter(source)
    with (source/'machine_knowledge.jsonl').open('a') as stream:
        stream.write('\n')
    with pytest.raises(ValueError, match='artifact changed'):
        fetch(a)


def test_measurement_filter_and_scholarly_category(source):
    a = MachineKnowledgeAdapter(source)
    req = {'target_concepts': ['quadruped', 'CPG']}
    r = a.fetch(['measurement_methods'], req, 'quadruped CPG')
    assert {x['knowledge_type'] for x in r['data']['knowledge']} == {'measurement_method'}
    r = a.fetch(['scholarly_evidence'], req, 'quadruped CPG', 'ScholarlyEvidenceConnector')
    assert r['source_category'] == 'machine_extracted_knowledge'
    assert r['human_verified'] is False


def test_duplicate_json_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        g.parse_final('{"answer_ja":"a","answer_ja":"b"}')


def test_inline_fabricated_citation_rejected(source):
    a = MachineKnowledgeAdapter(source)
    c = g.bounded_context(dict(g.CASES)['A'], g.fixture_task('A'), a)
    v = g.fixture_response(c)
    v['answer_ja'] += ' [knowledge_fake/paragraph_fake]'
    with pytest.raises(ValueError):
        g.validate_response(v, c, a)


def test_descriptive_provenance_references(source):
    a = MachineKnowledgeAdapter(source)
    c = g.bounded_context(dict(g.CASES)['A'], g.fixture_task('A'), a)
    v = g.fixture_response(c)
    r = next(iter(g.knowledge(c).values()))
    v['limitations'].append(r['knowledge_id'] + ' ' + r['provenance'][0]['paragraph_id'])
    assert g.validate_response(v, c, a)['valid']
    v['limitations'][-1] = r['knowledge_id'] + ' ' + a.paragraphs[1]['paragraph_id']
    with pytest.raises(ValueError, match='relation'):
        g.validate_response(v, c, a)


def test_resume_uses_checkpoint_no_regeneration(source, tmp_path, monkeypatch):
    output = tmp_path/'resume'
    original = g.validate_response
    def fail_once(*args):
        raise ValueError('simulated validator failure')
    monkeypatch.setattr(g, 'validate_response', fail_once)
    with pytest.raises(ValueError):
        g.run(output, source, dry_run=True)
    saved = (output/'responses.jsonl').read_text()
    assert json.loads(saved)['validation_status'] == 'pending'
    monkeypatch.setattr(g, 'validate_response', original)
    fixture = g.fixture_response
    def no_regenerate_a(context):
        assert context['user_request'] != dict(g.CASES)['A']
        return fixture(context)
    monkeypatch.setattr(g, 'fixture_response', no_regenerate_a)
    result = g.run(output, source, dry_run=True, resume=True)
    assert result['actual_api_calls'] == 0 and result['status'] == 'dry_run_complete'
    assert len((output/'responses.jsonl').read_text().splitlines()) == 4
    assert result['resume_history'][0]['failure_type'] == 'ValueError'


def test_schema_field_names_are_not_citations(source):
    a = MachineKnowledgeAdapter(source)
    c = g.bounded_context(dict(g.CASES)['D'], g.fixture_task('D'), a)
    v = g.fixture_response(c)
    v['unknown_or_unsupported'].append('knowledge_idとparagraph_idが必要です。')
    assert g.validate_response(v, c, a)['valid']
    v['unknown_or_unsupported'].append('knowledge_fake paragraph_fake')
    with pytest.raises(ValueError):
        g.validate_response(v, c, a)


def test_revalidate_only_forbids_incomplete_checkpoint(source, tmp_path, monkeypatch):
    output = tmp_path/'incomplete'
    def fail(*args):
        raise ValueError('simulated failure')
    monkeypatch.setattr(g, 'validate_response', fail)
    with pytest.raises(ValueError):
        g.run(output, source, dry_run=True)
    with pytest.raises(ValueError, match='All four'):
        g.run(output, source, dry_run=True, resume=True, revalidate_only=True)


def test_complete_revalidation_zero_api(source, tmp_path, monkeypatch):
    output = tmp_path/'complete'
    g.run(output, source, dry_run=True)
    m = json.loads((output/'manifest.json').read_text())
    m['status'] = 'failed'
    mk.save(output/'manifest.json',m)
    def forbidden(*args):
        raise AssertionError('No response regeneration or API calls permitted')
    monkeypatch.setattr(g, 'fixture_response', forbidden)
    monkeypatch.setattr(g.OpenAIProvider, 'generate', forbidden)
    result = g.run(output, source, dry_run=True, resume=True, revalidate_only=True)
    assert result['actual_api_calls'] == 0
    assert result['status'] == 'dry_run_complete'

import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario import machine_knowledge as m


@pytest.fixture
def source(tmp_path):
    raw = f'''<article><front><article-meta><article-id pub-id-type="doi">{m.DOI}</article-id><article-id pub-id-type="pmcid">{m.PMCID}</article-id><title-group><article-title>Test robot</article-title></title-group><permissions><license href="https://creativecommons.org/licenses/by/4.0/"/></permissions><abstract><p>Abstract</p></abstract></article-meta></front><body><sec><title>Methods</title><p>First</p><sec><title>Measures</title><p>Second</p></sec><p>Third</p></sec><sec><title>Results</title><p>Fourth</p><table-wrap><caption>Table</caption><table><tr><td>1</td></tr></table></table-wrap></sec><sec><title>Discussion</title><p>Limited sample.</p></sec></body><back><ref-list><ref id="r1">Reference only</ref></ref-list></back></article>'''.encode()
    (tmp_path/'source.xml').write_bytes(raw)
    record = dict(canonical_id='doi:'+m.DOI, source_identifier=m.PMCID, license='CC-BY', file='source.xml',
        content_sha256=m.digest(raw), validation=dict(identifiers=dict(doi=m.DOI, pmcid=m.PMCID), license='CC-BY'))
    path = tmp_path/'fulltext_manifest.jsonl'
    path.write_text(json.dumps(record)+'\n')
    return path


def candidate(paragraph):
    return dict(knowledge_type='other', **{k: [] for k in m.LISTS},
        population={k: None for k in ['description', 'sample_size', 'age', 'setting']},
        provenance=[{k: paragraph[k] for k in ['section_id', 'paragraph_id', 'section_title', 'source_text_sha256']}])


@pytest.mark.parametrize('field,value', [('content_sha256','bad'), ('canonical_id','doi:bad'), ('source_identifier','PMC1'), ('license','unknown')])
def test_manifest_mismatch(source, field, value):
    data=json.loads(source.read_text()); data[field]=value; source.write_text(json.dumps(data))
    with pytest.raises(ValueError): m.load_source(source)


@pytest.mark.parametrize('old,new', [(m.DOI,'wrong'), (m.PMCID,'PMC1'), ('</article>',''), ('creativecommons.org/licenses/by/4.0/','example.org')])
def test_xml_mismatch(source, old, new):
    p=source.parent/'source.xml'; p.write_text(p.read_text().replace(old,new))
    data=json.loads(source.read_text()); data['content_sha256']=m.digest(p.read_bytes()); source.write_text(json.dumps(data))
    with pytest.raises(ValueError): m.load_source(source)


def test_segments(source):
    root,_=m.load_source(source); a=m.segment(root); assert a==m.segment(root)
    assert [p['source_text'] for p in a[1]][1:5]==['First','Second','Third','Fourth']
    assert len({p['paragraph_id'] for p in a[1]})==len(a[1])
    assert a[2][0]['source_text']=='Reference only'
    assert all('Reference only' not in p['source_text'] for p in a[1])


@pytest.mark.parametrize('change', ['type','missing','paragraph','hash','section','verified','status','fall'])
def test_rejections(source, change):
    p=m.segment(m.load_source(source)[0])[1]; c=candidate(p[0])
    if change=='type': c['knowledge_type']='quality_score'
    if change=='missing': del c['findings']
    if change=='paragraph': c['provenance'][0]['paragraph_id']='hallucinated'
    if change=='hash': c['provenance'][0]['source_text_sha256']='bad'
    if change=='section': c['provenance'][0]['section_id']='section_9999'
    if change=='verified': c['extraction']={'human_verified':True}
    if change=='status': c['extraction']={'status':'human_verified'}
    if change=='fall': c['fall_risk']=0.9
    with pytest.raises(ValueError): m.validate_record(c,p)


def test_missing_values(source):
    p=m.segment(m.load_source(source)[0])[1]; m.validate_record(candidate(p[0]),p)
    assert 'fall' not in json.dumps(m.schema(p))


def test_secret_guard(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','sensitive-test-value')
    with pytest.raises(ValueError): m.save(tmp_path/'x',{'key':'sensitive-test-value'})
    assert not (tmp_path/'x').exists()


def test_pipeline_and_duplicates(source,tmp_path,monkeypatch):
    monkeypatch.setattr(m,'api_call',lambda _:pytest.fail('Network forbidden'))
    original=m.chunks
    monkeypatch.setattr(m,'chunks',lambda p: original(p)+original(p))
    out=tmp_path/'out'; result=m.run(source,out,True)
    assert result['actual_api_calls']==result['network_calls']==0
    assert result['duplicate_candidates']
    rows=[json.loads(s) for s in (out/'machine_knowledge.jsonl').read_text().splitlines()]
    assert all(r['extraction']['status']=='machine_extracted' and r['extraction']['human_verified'] is False and r['verification_status']=='pending' for r in rows)
    with pytest.raises(ValueError): m.run(source,out,True)


def test_request_boundary(source):
    root,doc=m.load_source(source); p=m.segment(root)[1]; data=m.payload(doc,p)
    assert data['store'] is False and data['reasoning']=={'effort':'medium'}
    assert not {'tools','temperature','sample_id','api_key'} & data.keys()
    assert set(json.loads(data['input'][1]['content']))=={'paper_metadata','source_paragraphs'}

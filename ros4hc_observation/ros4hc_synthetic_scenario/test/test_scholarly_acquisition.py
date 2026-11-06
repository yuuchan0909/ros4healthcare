"""Phase 5L offline fixtures only; no public API calls in unit tests."""
import copy
from dataclasses import fields
import io
import json
from pathlib import Path
from urllib.error import HTTPError
import pytest

from ros4hc_synthetic_scenario import scholarly_models as m
from ros4hc_synthetic_scenario import scholarly_sources as s
from ros4hc_synthetic_scenario import scholarly_acquisition as p


@pytest.mark.parametrize('value', ['10.1234/AbC', ' https://doi.org/10.1234/ABC ', 'http://doi.org/10.1234/ABC', 'doi: 10.1234/ABC'])
def test_doi_normalization(value):
    assert m.normalize_doi(value) == '10.1234/abc'


@pytest.mark.parametrize('value', [None, '', 'garbage', 'https://publisher.org/10.1234/abc', '10.1234/a?api_key=secret'])
def test_invalid_doi(value):
    assert m.normalize_doi(value) is None


def row(source='europepmc', **ids):
    receipt=dict(source=source, request_id=source, retrieved_at_utc='2026-11-06T00:00:00+00:00')
    r=s.record(source, s.identifiers(**ids), 'Test article', ['A'], 2026, 'Test journal', receipt)
    r.update(queries=['test'], discovery=True)
    return r


def test_dedup_bridge_fallback_determinism_and_conflicts():
    rows=[row('openalex',doi='10.1234/a',openalex_id='W1'),row(pmid='123',pmcid='PMC123'),
          row('crossref',doi='https://doi.org/10.1234/A',pmid='123')]
    before=copy.deepcopy(rows)
    candidates, report=p.deduplicate(rows)
    assert len(candidates)==1 and candidates[0]['canonical_id']=='doi:10.1234/a'
    assert candidates[0]['pmcid']=='PMC123' and len(candidates[0]['provenance'])==3
    assert rows==before and p.deduplicate(list(reversed(rows)))==(candidates,report)
    for ids, expected in [({'pmid':'123'},'pmid:123'),({'pmcid':'PMC123'},'pmcid:PMC123'),({'openalex_id':'W1'},'openalex_id:W1')]:
        assert m.canonical_identity(ids)==expected
    bad,_=p.deduplicate(rows+[row(doi='10.1234/b',pmid='123')])
    assert len(bad)==1 and bad[0]['knowledge_status']=='rejected' and 'doi' in bad[0]['identifier_conflicts']


@pytest.mark.parametrize('value,expected', [('CC BY','CC-BY'),('cc-by-4.0','CC-BY'),
    ('https://creativecommons.org/licenses/by/4.0/','CC-BY'),('CC0','CC0'),('public domain','CC0'),
    ('CC BY-NC','CC-BY-NC'),('https://creativecommons.org/licenses/by-nd/4.0/','CC-BY-ND'),
    (None,'unknown'),('publisher-specific','other'),('https://creativecommons.org.evil/licenses/by/4.0/','other')])
def test_license_normalization(value,expected):
    assert m.normalize_license(value)==expected


def candidate(license='CC BY', source='europepmc'):
    r=row(source,doi='10.1234/a',pmcid='PMC123')
    r['locations']=[dict(source=source, source_identifier='PMC123',location='https://official.invalid/item',
        license_raw=license,is_oa=True,api_fulltext_path='/PMC123/fullTextXML')]
    return p.deduplicate([r])[0][0]


@pytest.mark.parametrize('license', ['CC BY-NC', 'CC BY-ND','CC BY-SA','CC BY-NC-ND',None,'closed','unknown','publisher'])
def test_license_denied_metadata_only(license):
    c=candidate(license)
    assert p.rights_gate(c)['fulltext_acquisition_allowed'] is False
    assert c['access']['status']=='metadata_only'


def test_location_specific_permission_not_transferred():
    c=candidate('CC BY','openalex')
    assert p.rights_gate(c)['fulltext_acquisition_allowed'] is False
    c['access']['locations'].append(dict(source='europepmc',source_identifier='PMC123',location='api',
        license_raw=None,is_oa=True,api_fulltext_path='/PMC123/fullTextXML'))
    assert p.rights_gate(c)['fulltext_acquisition_allowed'] is False
    c['access']['locations'][-1]['license_raw']='CC0'
    assert p.rights_gate(c)['fulltext_acquisition_allowed'] is True
    c['knowledge_status']='rejected'
    assert p.rights_gate(c)['fulltext_acquisition_allowed'] is False


def xml(license='https://creativecommons.org/licenses/by/4.0/', pmcid='123', doi='10.1234/a'):
    return f'''<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
    <article-id pub-id-type="pmc">{pmcid}</article-id><article-id pub-id-type="doi">{doi}</article-id>
    <permissions><license xlink:href="{license}"><license-p>Licensed article</license-p></license></permissions>
    </article-meta></front><body><p>Fixture content only.</p></body></article>'''.encode()


def test_xml_identity_rights_and_entities():
    c=candidate();p.rights_gate(c);loc=c['access']['locations'][0]
    assert p.validate_fulltext(xml(),c,loc)['license']=='CC-BY'
    for body in [xml(pmcid='999'),xml(doi='10.1234/wrong'),xml(license='https://creativecommons.org/licenses/by-nc/4.0/'),
                 b'<html/>',b'<!DOCTYPE article [<!ENTITY x "spam">]><article/>']:
        with pytest.raises(ValueError):p.validate_fulltext(body,c,loc)


class Response(io.BytesIO):
    pass


class Opener:
    def __init__(self, responses):self.responses=list(responses);self.calls=0
    def open(self, req, timeout):
        self.calls+=1
        value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        return Response(value)


def client(tmp_path,responses):
    sleeps=[];opener=Opener(responses)
    c=s.OfficialAPIClient(tmp_path/'cache',opener=opener,sleep=sleeps.append,clock=lambda:100,interval=0)
    return c,opener,sleeps


def test_metadata_cache_projection_hash_and_key_not_persisted(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENALEX_API_KEY','SUPER_SECRET_DO_NOT_PERSIST')
    c,o,_=client(tmp_path,[b'{"results":[],"abstract":"copyrighted"}'])
    a,r=c.get('openalex','/works',{'search':'test','per_page':10})
    b,r2=c.get('openalex','/works',{'per_page':10,'search':'test'})
    assert a==b=={'results':[]} and o.calls==1 and r2['cache_hit'] is True
    for f in (tmp_path/'cache').iterdir():
        assert 'SUPER_SECRET_DO_NOT_PERSIST' not in f.read_text() and 'copyrighted' not in f.read_text()
    with pytest.raises(ValueError):s.save(tmp_path/'secret.json',{'key':'SUPER_SECRET_DO_NOT_PERSIST'})


@pytest.mark.parametrize('source,path,params,xmlflag', [('publisher','/pdf',{},False),
    ('europepmc','https://publisher.org/a.pdf',{},True),('europepmc','/PMC123/fullTextXML',{},False),
    ('openalex','/works',{'api_key':'secret'},False),('crossref','/../../secret',{},False)])
def test_publisher_download_and_endpoint_injection_rejected(source,path,params,xmlflag,tmp_path):
    c,o,_=client(tmp_path,[])
    with pytest.raises(ValueError):c.get(source,path,params,xmlflag)
    assert o.calls==0


def test_timeouts_and_retry_bounds(tmp_path):
    c,o,sleeps=client(tmp_path,[TimeoutError(),TimeoutError()])
    with pytest.raises(s.FetchError):c.get('openalex','/works',{})
    assert o.calls==2 and 2 in sleeps


def test_429_backoff_and_success(tmp_path):
    err=HTTPError('https://api.openalex.org/works',429,'rate limit',{'Retry-After':'3'},None)
    c,o,sleeps=client(tmp_path,[err,b'{"results":[]}'])
    c.get('openalex','/works',{})
    assert o.calls==2 and 3 in sleeps


def test_long_retry_after_defers_without_early_retry(tmp_path):
    c,o,_=client(tmp_path,[HTTPError('https://api.openalex.org/works',429,'x',{'Retry-After':'120'},None)])
    with pytest.raises(s.FetchError):c.get('openalex','/works',{})
    assert o.calls==1


def test_malformed_json_and_response_shape(tmp_path):
    c,o,_=client(tmp_path,[b'<html>not API JSON</html>'])
    with pytest.raises(s.FetchError):c.get('openalex','/works',{})
    assert o.calls==1
    with pytest.raises(ValueError):s.object_list({'wrong':'shape'})
    with pytest.raises(ValueError):s.openalex_record({'id':'W1'}, {})


def test_retraction_propagation_without_marking_notice_as_retracted():
    a=row('openalex',doi='10.1234/a');a['integrity_signals']=[dict(source='openalex',retracted=False)]
    b=row(doi='10.1234/a');b['integrity_signals']=[dict(source='europepmc',retracted=True)]
    c=p.deduplicate([a,b])[0][0]
    assert c['integrity']['retracted'] is True and c['knowledge_status']=='rejected'
    notice=s.crossref_record({'DOI':'10.1234/notice','title':['Retraction notice'],
        'update-to':[{'DOI':'10.1234/a','type':'retraction'}]},dict(retrieved_at_utc='fixed'))
    assert p.deduplicate([notice])[0][0]['integrity']['retracted'] is None


def test_domain_neutral_schema_extraction_not_implemented():
    assert all('fall' not in f.name for f in fields(m.KnowledgeCandidate))
    c=m.KnowledgeCandidate('pmid:123').to_dict()
    assert c['knowledge_status']=='discovered' and c['quality_signals']['sample_size'] is None
    assert 'human_verified' not in m.AUTOMATED_STATUSES
    with pytest.raises(NotImplementedError):m.extract_knowledge(c)
    with pytest.raises(ValueError):m.KnowledgeExtractionCandidate('pmid:123',knowledge_type='diagnosis')


def test_dry_run_never_calls_provider_and_does_not_verify(tmp_path):
    class NoCalls:
        network_attempts=0
        requests=[]
        def get(self,*args,**kwargs):raise AssertionError('No network allowed')
    out=tmp_path/'dry';manifest=p.run(out,True,NoCalls())
    assert manifest['network_attempts']==0 and manifest['fulltext_acquired']==0
    assert manifest['candidate_count']==1 and manifest['duplicate_records_removed']==5
    assert all(json.loads(l)['knowledge_status'] in m.AUTOMATED_STATUSES for l in (out/'candidates.jsonl').read_text().splitlines())
    assert {f.name for f in out.iterdir()} >= {'manifest.json','queries.json','candidates.jsonl','dedup_report.json',
        'rights_report.json','integrity_report.json','acquisition_report.json','fulltext_manifest.jsonl','report_ja.md'}
    with pytest.raises(ValueError):p.run(out,True)


class FixtureAPI:
    """Complete in-memory official envelopes; no urllib or external API access."""
    def __init__(self, license='CC BY', separate=False):
        self.license=license;self.separate=separate;self.requests=[];self.network_attempts=0;self.xml_calls=0
    def get(self,source,path,params=None,xml=False):
        from urllib.parse import unquote
        self.network_attempts+=1
        receipt=dict(source=source,path=path,parameters=params or {},request_id=str(self.network_attempts),
                     retrieved_at_utc='2026-11-06T00:00:00+00:00',response_sha256='fixture')
        self.requests.append(receipt)
        if xml:
            self.xml_calls+=1
            n=int(path.split('/')[1][3:])-1000
            q,i=divmod(n,10)
            doi=f'10.1234/{q}_{i}' + ('e' if self.separate else '')
            return globals()['xml'](pmcid=str(1000+n),doi=doi),receipt
        if source=='crossref':
            if path=='/works':
                doi=params['filter'].split(':',1)[1]
                items=[dict(DOI='10.1234/notice',**{'update-to':[{'DOI':doi,'type':'retraction'}]})] if doi=='10.1234/0_0' else []
                return {'message':{'items':items}},receipt
            doi=unquote(path[len('/works/'):])
            return {'message':{'DOI':doi,'title':['Crossref merged title'],'type':'journal-article'}},receipt
        q=p.QUERIES.index(params['search'] if source=='openalex' else params['query'])
        items=[]
        for i in range(10):
            n=q*10+i
            doi=f'10.1234/{q}_{i}' + (source[0] if self.separate else '')
            if source=='openalex':
                items.append(dict(id=f'https://openalex.org/W{n+1}',doi=doi,title='OpenAlex test title',
                    publication_year=2026,is_retracted=False,authorships=[],locations=[]))
            else:
                items.append(dict(id=str(n+1),source='MED',doi=doi,pmcid=f'PMC{1000+n}',title='Europe PMC title',
                    pubYear='2026',isOpenAccess='Y',license=self.license,authorList={'author':[]}))
        return ({'results':items} if source=='openalex' else {'resultList':{'result':items}}),receipt
    def cache_approved_xml(self,body,receipt):
        pass


def test_complete_mock_pipeline_merge_integrity_and_five_fulltexts(tmp_path):
    api=FixtureAPI();out=tmp_path/'pipeline'
    manifest=p.run(out,client=api)
    rows=[json.loads(l) for l in (out/'candidates.jsonl').read_text().splitlines()]
    assert manifest['candidate_count']==30 and manifest['duplicate_records_removed']==30
    assert manifest['source_counts']=={'openalex':30,'europepmc':30,'crossref':30}
    assert api.xml_calls==manifest['fulltext_acquired']==5
    rejected=next(c for c in rows if c['doi']=='10.1234/0_0')
    assert rejected['integrity']['retracted'] is True and rejected['knowledge_status']=='rejected'
    assert not rejected['access']['fulltext_acquisition_allowed']
    assert all(c['title']=='Crossref merged title' and c['knowledge_status'] in m.AUTOMATED_STATUSES for c in rows)
    for entry in map(json.loads,(out/'fulltext_manifest.jsonl').read_text().splitlines()):
        assert s.sha((out/entry['file']).read_bytes())==entry['content_sha256']


def test_unknown_rights_never_fetch_xml_and_global_cap(tmp_path):
    api=FixtureAPI(license=None,separate=True)
    manifest=p.run(tmp_path/'cap',client=api)
    assert manifest['candidate_count']==50 and api.xml_calls==0
    assert manifest['fulltext_acquired']==0
    report=json.loads((tmp_path/'cap/dedup_report.json').read_text())
    assert len(report['excluded_by_global_cap'])==10
    assert manifest['source_counts']['crossref']==50


def test_approved_xml_cache_preserves_original_bytes_and_hash(tmp_path):
    body=xml().replace(b'<article ',b'<?xml version="1.0" encoding="ISO-8859-1"?><article ').replace(b'Fixture content only.',b'caf\xe9')
    api,opener,_=client(tmp_path,[body])
    candidate_record=candidate();p.rights_gate(candidate_record)
    received,receipt=api.get('europepmc','/PMC123/fullTextXML',xml=True)
    p.validate_fulltext(received,candidate_record,candidate_record['access']['locations'][0])
    api.cache_approved_xml(received,receipt)
    cached,receipt2=api.get('europepmc','/PMC123/fullTextXML',xml=True)
    assert cached==received==body and opener.calls==1
    assert s.sha(cached)==receipt2['response_sha256'] and receipt2['cache_hit'] is True

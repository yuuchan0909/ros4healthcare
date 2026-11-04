"""Synthetic-only registry engineering tests; these are not academic findings."""
import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.literature_registry import (
    STATUSES, FINDINGS, RELATIONSHIPS, DIRECTIONS, PUBLICATION_STATUSES,
    validate_paper, validate_chunk, validate_registry, normalize_doi, read_jsonl)
from ros4hc_synthetic_scenario.literature_curation import (
    demo_records, convert_registry, export_registry, validate_main, convert_main, demo_main)
from ros4hc_synthetic_scenario.literature_coverage import coverage_report, manifest
from ros4hc_synthetic_scenario.literature_corpus import validate_record, load_corpus
from ros4hc_synthetic_scenario.literature_retrieval import LexicalRetrievalBaseline
from ros4hc_synthetic_scenario.evidence import build_bundle


@pytest.fixture
def registry():
    return demo_records()


def normalized(registry):
    return validate_registry(*registry, mode='synthetic')


def test_valid_paper_and_unknown(registry):
    p = validate_paper(registry[0][0])
    assert p['population'] is None and p['doi'] is None and p['sample_size'] is None
    assert p['authors'] is None and p['study_design'] is None
    assert p['synthetic_fixture'] is True


@pytest.mark.parametrize('field', ['paper_id','title'])
def test_required_paper(registry,field):
    p=registry[0][0]; del p[field]
    with pytest.raises(ValueError): validate_paper(p)


@pytest.mark.parametrize('field', ['paper_id','chunk_id','evidence_summary'])
def test_required_chunk(registry,field):
    c=registry[1][0]; c[field]=' '
    with pytest.raises(ValueError): validate_chunk(c)


@pytest.mark.parametrize('field,value', [('year','2026'),('year',True),('year',10000),
    ('sample_size',False),('sample_size',-1),('authors','author'),('authors',[1]),
    ('sensor_modalities',{}),('peer_reviewed','true'),('publication_type','magic'),
    ('publication_status','future_magic'),('curation_status','magic'),('curation_status',None),
    ('synthetic_fixture',None),('notes',{'split_label':'hidden'}),('title',[])])
def test_invalid_paper_type(registry,field,value):
    p=registry[0][0];p[field]=value
    with pytest.raises(ValueError): validate_paper(p)


@pytest.mark.parametrize('field,value',[('finding_type','causal_proof'),('relationship','cures'),
    ('direction','healthy'),('short_quote',1),('limitations','text'),('page',3),
    ('source_location_verified',None),('source_location_verified','yes'),
    ('curation_status','magic'),('curator_notes',{'future_target':82})])
def test_invalid_chunk_type(registry,field,value):
    c=registry[1][0];c[field]=value
    with pytest.raises(ValueError): validate_chunk(c)


@pytest.mark.parametrize('status',STATUSES)
def test_statuses(registry,status):
    p=registry[0][0];p['curation_status']=status
    c=registry[1][0];c['curation_status']=status
    assert validate_paper(p)['curation_status']==status
    assert validate_chunk(c)['curation_status']==status


@pytest.mark.parametrize('status',PUBLICATION_STATUSES)
def test_publication_statuses(registry,status):
    p=registry[0][0];p['publication_status']=status
    assert validate_paper(p)['publication_status']==status


@pytest.mark.parametrize('field,values',[('finding_type',FINDINGS),('relationship',RELATIONSHIPS),('direction',DIRECTIONS)])
def test_neutral_enums(registry,field,values):
    for value in values:
        c=copy.deepcopy(registry[1][0]);c[field]=value
        assert validate_chunk(c)[field]==value


@pytest.mark.parametrize('form',['10.1234/TEST','https://doi.org/10.1234/TEST',
    'doi:10.1234/TEST',' DOI: 10.1234/TEST ', 'http://dx.doi.org/10.1234/TEST'])
def test_doi_normalization(form):
    assert normalize_doi(form)=='10.1234/test'


@pytest.mark.parametrize('value',['invalid','10.123/x','10.1234/has space',42,True])
def test_invalid_doi(value):
    with pytest.raises(ValueError): normalize_doi(value)


def test_duplicate_ids_and_orphan(registry):
    p,c=registry
    with pytest.raises(ValueError): validate_registry(p+p[:1],c,'synthetic')
    with pytest.raises(ValueError): validate_registry(p,c+c[:1],'synthetic')
    c[0]['paper_id']='synthetic_paper_9999'
    with pytest.raises(ValueError): validate_registry(p,c,'synthetic')


def test_doi_consistency(registry):
    p,c=registry
    p[0]['doi']='doi:10.1234/TEST';p[1]['doi']='https://doi.org/10.1234/test'
    with pytest.raises(ValueError): validate_registry(p,c,'synthetic')


def test_valid_chunk_quote_location(registry):
    c=validate_chunk(registry[1][0])
    assert c['short_quote'] is None
    c['short_quote']='Synthetic short quote for format test only'
    assert validate_chunk(c)['short_quote']==c['short_quote']
    assert c['page']=='1' and c['section']=='Synthetic test section'


def test_source_location_warning_and_contradiction(registry):
    p,c=registry
    c[0].update(section=None,page=None,source_location_verified=False)
    report=validate_registry(p,c,'synthetic')[2]
    assert any(w['code']=='missing_source_location' for w in report['warnings'])
    c[0]['source_location_verified']=True
    with pytest.raises(ValueError): validate_registry(p,c,'synthetic')


def test_verified_conversion_and_excluded(registry):
    p,c,r=normalized(registry)
    assert len(p)==3 and p[2]['curation_status']=='excluded'
    result=convert_registry(p,c,'synthetic')
    assert len(result)==2
    assert all(x['paper_id']=='synthetic_paper_0001' for x in result)
    diagnostic=convert_registry(p,c,'synthetic',True)
    assert len(diagnostic)==4 and all(x['paper_id']!='synthetic_paper_0003' for x in diagnostic)
    assert sum(x['curation_provenance']['diagnostic_unverified'] for x in diagnostic)==2


def test_both_levels_must_be_verified(registry):
    p,c=registry;c[0]['curation_status']='candidate'
    assert len(convert_registry(p,c,'synthetic'))==1
    p[0]['curation_status']='candidate'
    assert convert_registry(p,c,'synthetic')==[]


def test_synthetic_real_separation(registry):
    with pytest.raises(ValueError): convert_registry(*registry)
    p,c=registry;c[0]['synthetic_fixture']=False
    with pytest.raises(ValueError): validate_registry(p,c,'synthetic')
    p[0]['synthetic_fixture']=False
    with pytest.raises(ValueError): validate_paper(p[0])
    assert convert_registry([],[])==[]


def test_no_invented_chunk_metadata(registry):
    p,c=registry
    p[0]['population']='Synthetic population placeholder'
    c[0].update(population=None,sensor_modalities=None,measured_variables=None)
    record=convert_registry(p,c,'synthetic')[0]
    assert record['population'] is None and record['sensor_modalities'] is None
    assert record['measured_variables'] is None


def test_provenance_retrieval_and_bundle(registry):
    p,c=registry
    p[0].update(doi='https://doi.org/10.1234/SYNTHETIC',url='https://example.invalid/synthetic',
                authors=['Synthetic test author'],year=2026)
    c[0].update(figure_or_table='Synthetic table 1',limitations=['Synthetic limitation'],
                curator_notes='Evaluator-side note: do not export',short_quote='Synthetic quote not exported')
    records=convert_registry(p,c,'synthetic')
    assert records==[validate_record(r) for r in records]
    facts={'hr':{'mean_hr_bpm':82},'acc':{'activity_magnitude_mean':1}}
    bundle=build_bundle(facts,LexicalRetrievalBaseline(records))
    evidence=next(r for r in bundle['evidence'] if r['chunk_id']==c[0]['chunk_id'])
    prov=evidence['provenance'];loc=prov['curation']
    assert prov['paper_id']==p[0]['paper_id'] and prov['title']==p[0]['title']
    assert prov['doi']=='10.1234/synthetic' and prov['url']==p[0]['url']
    assert loc['page']=='1' and loc['section']==c[0]['section'] and loc['figure_or_table']==c[0]['figure_or_table']
    assert loc['limitations']==c[0]['limitations'] and loc['source_location_verified']
    assert evidence['evidence_text']==c[0]['evidence_summary']
    assert evidence['synthetic_fixture'] is True and len(evidence['corpus_record_sha256'])==64
    text=json.dumps(bundle)
    assert 'Evaluator-side note' not in text and 'short_quote' not in text
    assert bundle['sensor_facts']==facts


def test_coverage_and_manifest(registry):
    p,c,_=normalized(registry)
    report=coverage_report(p,c,'synthetic')
    counts=report['registry_counts']
    assert counts['papers']['sensor_modalities']['hr']==1
    assert counts['chunks']['measured_variables']['mean_hr_bpm']==2
    assert counts['chunks']['population']['<unknown>']==5
    assert counts['chunks']['task_or_state']['<unknown>']==5
    assert counts['chunks']['finding_type']['method']==5
    assert counts['papers']['year']['<unknown>']==3
    assert counts['papers']['curation_status']['excluded']==1
    assert len(report['multimodal_papers'])==1 and len(report['multimodal_chunks'])==2
    assert report['verified_multimodal_combinations']==[['activity','hr']]
    assert report['verified_hr_activity_chunk_present'] is True
    assert next(g for g in report['coverage_gaps'] if g['topic']=='balance')['verified_evidence_paper_count']==0
    m=manifest(p,c,'synthetic',report,2)
    assert m['corpus_version']=='phase5g-v1' and m['synthetic_fixture_count']==8 and m['real_record_count']==0
    assert m['verified_paper_count']==1 and m['verified_chunk_count']==2
    assert m==manifest(p,c,'synthetic',report,2) and 'generated_at' not in m


def test_known_coverage(registry):
    p,c=registry
    p[0].update(population='Synthetic population placeholder',year=2026,tasks_or_states=['Synthetic task'])
    c[0].update(population='Synthetic population placeholder',task_or_state='Synthetic task')
    p,c,_=normalized(registry);r=coverage_report(p,c,'synthetic')
    assert r['registry_counts']['papers']['year']['2026']==1
    assert r['registry_counts']['chunks']['population']['Synthetic population placeholder']==1
    assert r['registry_counts']['chunks']['task_or_state']['Synthetic task']==1


def test_duplicate_summary_warnings(registry):
    p,c=registry
    c[1]['page']=c[0]['page'];c[1]['evidence_summary']=c[0]['evidence_summary']
    r=normalized(registry)[2]
    assert {'same_paper_page_summary','identical_normalized_summary'} <= {w['code'] for w in r['warnings']}
    c[1]['evidence_summary']='  '+c[0]['evidence_summary'].upper()+'  '
    assert 'identical_normalized_summary' in {w['code'] for w in normalized(registry)[2]['warnings']}


@pytest.mark.parametrize('key',['ground_truth_state','expected_state','hidden_scenario','split_label',
                               'future_target','future_numeric_target','evaluator_annotations'])
def test_leakage_rejected(registry,key):
    p,c=registry
    c[0][key]='opaque'
    with pytest.raises(ValueError): convert_registry(p,c,'synthetic')
    del c[0][key]
    records=convert_registry(p,c,'synthetic');records[0]['curation_provenance'][key]='opaque'
    with pytest.raises(ValueError): LexicalRetrievalBaseline(records)
    facts={'hr':{'metadata':{'quality':{key:'opaque'}}},'acc':{}}
    with pytest.raises(ValueError): build_bundle(facts,LexicalRetrievalBaseline(convert_registry(p,c,'synthetic')))


def test_deterministic_files_and_no_mutation(registry,tmp_path):
    before=copy.deepcopy(registry)
    export_registry(*registry,tmp_path/'a',mode='synthetic')
    export_registry(registry[0][::-1],registry[1][::-1],tmp_path/'b',mode='synthetic')
    assert registry==before
    for path in (tmp_path/'a').iterdir():
        assert path.read_bytes()==(tmp_path/'b'/path.name).read_bytes()
    assert len(load_corpus(tmp_path/'a/verified_literature_corpus.jsonl'))==2
    export_registry(*registry,tmp_path/'diag',mode='synthetic',include_unverified=True)
    assert not (tmp_path/'diag/verified_literature_corpus.jsonl').exists()
    assert (tmp_path/'diag/diagnostic_literature_corpus.jsonl').exists()


def test_cli_and_invalid_report(registry,tmp_path,monkeypatch):
    from ros4hc_synthetic_scenario.evidence import write_jsonl
    p,c=registry;write_jsonl(tmp_path/'p',p);write_jsonl(tmp_path/'c',c)
    args=['tool','--papers',str(tmp_path/'p'),'--evidence',str(tmp_path/'c'),'--mode','synthetic']
    monkeypatch.setattr('sys.argv',args+['--output-report',str(tmp_path/'report')]);validate_main()
    assert json.loads((tmp_path/'report').read_text())['valid']
    monkeypatch.setattr('sys.argv',args+['--output',str(tmp_path/'converted')]);convert_main()
    monkeypatch.setattr('sys.argv',['tool','--papers',str(tmp_path/'p'),'--evidence',str(tmp_path/'c'),
                                      '--output-report',str(tmp_path/'bad')])
    with pytest.raises(SystemExit):validate_main()
    assert json.loads((tmp_path/'bad').read_text())['valid'] is False
    monkeypatch.setattr('sys.argv',['tool','--output',str(tmp_path/'demo')]);demo_main()
    assert len(read_jsonl(tmp_path/'demo/demo_retrieval.jsonl')[0]['evidence'])==2


def test_json_duplicate_keys_and_placeholder_templates(tmp_path):
    (tmp_path/'bad').write_text('{"paper_id":"a","paper_id":"b"}\n')
    with pytest.raises(ValueError):read_jsonl(tmp_path/'bad')
    root=Path(__file__).parents[1]/'ros4hc_synthetic_scenario/templates'
    paper=read_jsonl(root/'papers_template.jsonl')[0]
    chunk=read_jsonl(root/'evidence_chunks_template.jsonl')[0]
    assert paper['title'] is None and chunk['evidence_summary'] is None
    with pytest.raises(ValueError): validate_paper(paper)
    with pytest.raises(ValueError): validate_chunk(chunk)


def test_no_network_or_api_dependencies():
    import ast
    root=Path(__file__).parents[1]/'ros4hc_synthetic_scenario'
    forbidden={'openai','anthropic','google','requests','urllib','socket','http','torch','chromadb','subprocess'}
    for name in ('literature_registry','literature_curation','literature_coverage'):
        for node in ast.walk(ast.parse((root/(name+'.py')).read_text())):
            if isinstance(node,ast.Import):assert not {a.name.split('.')[0] for a in node.names}&forbidden
            if isinstance(node,ast.ImportFrom):assert (node.module or '').split('.')[0] not in forbidden

"""Condition-aware, synthetic-only storage tests; no clinical claims."""
import copy
import csv
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.fall_risk_evidence import (
    normalize_fall_risk, condition_warnings, condition_coverage, CATEGORIES)
from ros4hc_synthetic_scenario.literature_registry import validate_registry
from ros4hc_synthetic_scenario.literature_curation import demo_records, convert_registry, export_registry
from ros4hc_synthetic_scenario.literature_retrieval import LexicalRetrievalBaseline
from ros4hc_synthetic_scenario.evidence import build_bundle
from ros4hc_synthetic_scenario.literature_coverage import coverage_report


def synthetic_conditions():
    return {'value_origin':'paper_reported',
        'population':{'description':'Synthetic population for format testing only', 'sample_size':12,
                      'age':'Synthetic age description', 'setting':'Synthetic lab',
                      'inclusion_criteria':['Synthetic inclusion'], 'exclusion_criteria':['Synthetic exclusion']},
        'measurement':{'sensor_modalities':['imu','pressure'], 'devices':['Synthetic device'],
            'protocol':'Synthetic protocol; no validated measurement', 'measured_variables':['synthetic_indicator'],
            'test_name':'Synthetic test', 'measurement_distance_or_duration':'Synthetic duration', 'body_location':['Synthetic location']},
        'risk_indicator':{'indicator_name':'synthetic_indicator','threshold_value':123,
                          'threshold_operator':'<=','unit':'synthetic_unit','unit_required':True,
                          'reference_criterion':'Synthetic reference, not a clinical criterion'},
        'fall_outcome':{'fall_definition':'Synthetic outcome definition', 'ascertainment_method':'Synthetic diary',
                        'prospective_or_retrospective':'prospective','followup_duration':'Synthetic follow-up duration'},
        'results':{'result_summary':'Synthetic numerical format test; no scientific claim', 'auc':0.75,
                   'sensitivity':0.6,'specificity':0.7,'effect_size':{'value':1.2,'measure':'Synthetic effect','unit':None},
                   'confidence_interval':{'lower':0.8,'upper':1.6,'confidence_level':0.95,'measure':'Synthetic effect'},
                   'p_value':0.03,'p_value_operator':'<'},
        'limitations':{'paper_reported_limitations':['Synthetic limited population']},
        'knowledge_base':{'risk_factor_category':'motor_function','evidence_strength_note':'Synthetic prospective cohort format note'},
        'provenance':{'section':'Synthetic section','page':'9','table':'Synthetic table','figure':'Synthetic figure',
                      'doi':'doi:10.1234/SYNTHETIC','url':'https://example.invalid/synthetic'}}


@pytest.fixture
def registry():
    p,c=demo_records();p[0]['doi']='10.1234/synthetic'
    c[0]['fall_risk']=synthetic_conditions()
    return p,c


@pytest.mark.parametrize('group', ['population','measurement','risk_indicator','fall_outcome','results',
                                   'limitations','knowledge_base','provenance'])
def test_professor_fields_retained(registry,group):
    p,c,_=validate_registry(*registry,'synthetic')
    expected=normalize_fall_risk(synthetic_conditions())
    assert c[0]['fall_risk'][group]==expected[group]
    converted=convert_registry(p,c,'synthetic')[0]
    assert converted['curation_provenance']['fall_risk'][group]==expected[group]


def test_unknown_no_inference(registry):
    p,c=registry;c[0]['fall_risk']={}
    p,c,_=validate_registry(p,c,'synthetic')
    f=c[0]['fall_risk']
    assert all(value is None for group in f.values() if isinstance(group,dict) for value in group.values())
    assert f['value_origin'] is None
    assert f['population']['description'] is None
    assert f['measurement']['sensor_modalities'] is None
    assert convert_registry(p,c,'synthetic')[0]['curation_provenance']['fall_risk']==f


@pytest.mark.parametrize('value',['unknown','not reported','不明'])
def test_unknown_text_preserved(value,registry):
    p,c=registry;c[0]['fall_risk']={'population':{'description':value}}
    p,c,r=validate_registry(p,c,'synthetic')
    assert c[0]['fall_risk']['population']['description']==value
    assert 'missing_population' in {w['code'] for w in r['warnings']}


@pytest.mark.parametrize('group,key,value',[
    ('results','auc','0.75'),('results','auc',True),('results','auc',1.1),
    ('results','sensitivity',-0.2),('results','specificity',float('nan')),
    ('results','p_value',float('inf')),('results','effect_size',{'value':'1.2'}),
    ('results','confidence_interval',{'lower':2,'upper':1}),
    ('results','confidence_interval',{'confidence_level':95}),
    ('risk_indicator','threshold_value','123'),('risk_indicator','threshold_value',True),
    ('risk_indicator','threshold_operator','diagnoses'),('population','sample_size',True),
    ('knowledge_base','risk_factor_category','automatically_high_risk')])
def test_invalid_results(group,key,value):
    with pytest.raises(ValueError):normalize_fall_risk({group:{key:value}})


def test_threshold_unit_error_and_warning(registry):
    p,c=registry;f=c[0]['fall_risk'];f['risk_indicator']['unit']=None
    with pytest.raises(ValueError):validate_registry(p,c,'synthetic')
    f['risk_indicator']['unit_required']=None
    _,_,r=validate_registry(p,c,'synthetic')
    assert 'threshold_unit_not_reported' in {w['code'] for w in r['warnings']}


def test_missing_applicability_warning(registry):
    p,c=registry;c[0]['fall_risk']={'risk_indicator':{'threshold_value':123}}
    _,_,r=validate_registry(p,c,'synthetic')
    codes={w['code'] for w in r['warnings']}
    assert {'missing_population','missing_measurement_protocol','missing_fall_definition','missing_followup',
            'threshold_without_applicability_metadata','threshold_without_reported_performance'} <= codes


def test_result_missing_source_warning(registry):
    p,c=registry
    c[0].update(section=None,page=None,source_location_verified=False)
    c[0]['fall_risk']['provenance']={'doi':'10.1234/synthetic'}
    _,_,r=validate_registry(p,c,'synthetic')
    assert {'missing_source_location','result_without_source_location'} <= {w['code'] for w in r['warnings']}


def test_structured_location_sufficient_and_doi_consistent(registry):
    p,c=registry;c[0].update(section=None,page=None,figure_or_table=None)
    p,c,r=validate_registry(p,c,'synthetic')
    assert not any(w['code']=='missing_source_location' and w.get('identity')==[c[0]['paper_id'],c[0]['chunk_id']] for w in r['warnings'])
    assert convert_registry(p,c,'synthetic')[0]['curation_provenance']['fall_risk']['provenance']['page']=='9'
    c[0]['fall_risk']['provenance']['doi']='10.1234/different'
    with pytest.raises(ValueError):validate_registry(p,c,'synthetic')


@pytest.mark.parametrize('origin',['inferred','llm_generated','sensor_derived'])
def test_inferred_values_rejected(origin):
    with pytest.raises(ValueError):normalize_fall_risk({'value_origin':origin})


@pytest.mark.parametrize('key',['ground_truth_state','expected_state','future_target','split_label'])
def test_nested_leakage_rejected(registry,key):
    p,c=registry;c[0]['fall_risk']['results'][key]=123
    with pytest.raises(ValueError):convert_registry(p,c,'synthetic')


def test_phase5f_bundle_preserves_conditions_without_judgment(registry):
    records=convert_registry(*registry,'synthetic')
    assert records[0]['sensor_modalities']==['imu','pressure']
    assert records[0]['measured_variables']==['synthetic_indicator']
    bundle=build_bundle({'hr':{'mean_hr_bpm':82},'acc':{}},LexicalRetrievalBaseline(records))
    item=next(r for r in bundle['evidence'] if r['chunk_id']=='chunk_0001')
    assert item['provenance']['curation']['fall_risk']==normalize_fall_risk(synthetic_conditions())
    assert 'risk_score' not in bundle and 'assessment' not in bundle and 'fall_probability' not in bundle
    assert all(r['synthetic_fixture'] is True for r in bundle['evidence'])


def test_verified_excluded_and_real_separation(registry):
    assert len(convert_registry(*registry,'synthetic'))==2
    with pytest.raises(ValueError):convert_registry(*registry,'real')
    p,c=registry;p[0]['curation_status']='excluded'
    assert convert_registry(p,c,'synthetic')==[]


@pytest.mark.parametrize('category',CATEGORIES)
def test_categories(category):
    assert normalize_fall_risk({'knowledge_base':{'risk_factor_category':category}})['knowledge_base']['risk_factor_category']==category


@pytest.mark.parametrize('ascertainment',['prospective','retrospective','mixed','unknown','not_reported'])
def test_ascertainment(ascertainment):
    assert normalize_fall_risk({'fall_outcome':{'prospective_or_retrospective':ascertainment}})['fall_outcome']['prospective_or_retrospective']==ascertainment


def test_coverage(registry):
    p,c,_=validate_registry(*registry,'synthetic')
    r=coverage_report(p,c,'synthetic')['fall_risk_coverage']
    assert r['registry']['chunk_count']==5 and r['verified_retrieval']['chunk_count']==2
    fields=r['registry']['fields']
    for key in ('population','sensor_modality','measurement_variable','measurement_protocol','threshold',
                'fall_definition','followup','risk_factor_category','auc','sensitivity','specificity',
                'effect_size','confidence_interval','p_value','limitations','evidence_strength_note'):
        assert fields[key]['reported_chunks']==1 and fields[key]['unknown_or_missing_chunks']==4
    assert fields['risk_factor_category']['values']=={'motor_function':1}
    assert r['registry']['provenance']['located_chunks']==5


def test_determinism_and_no_mutation(registry,tmp_path):
    before=copy.deepcopy(registry)
    export_registry(*registry,tmp_path/'a',mode='synthetic')
    export_registry(registry[0][::-1],registry[1][::-1],tmp_path/'b',mode='synthetic')
    for path in (tmp_path/'a').iterdir():assert path.read_bytes()==(tmp_path/'b'/path.name).read_bytes()
    assert registry==before


def test_templates():
    root=Path(__file__).parents[1]/'ros4hc_synthetic_scenario/templates'
    sheet=json.loads((root/'fall_risk_paper_template.jsonl').read_text())
    assert {'title','authors','venue','year'} <= set(sheet['paper'])
    f=sheet['evidence_chunks'][0]['fall_risk']
    assert set(normalize_fall_risk({}))==set(f)
    assert f['risk_indicator']['threshold_value'] is None
    with (root/'fall_risk_paper_template_ja.csv').open(encoding='utf-8-sig') as stream:
        rows=list(csv.reader(stream))
    assert '論文題名' in rows[0] and len(rows)==3 and all(v=='' for v in rows[2])


def test_duplicate_and_orphan_remain_rejected(registry):
    p,c=registry
    with pytest.raises(ValueError):validate_registry(p,c+c[:1],'synthetic')
    c[0]['paper_id']='synthetic_paper_9999'
    with pytest.raises(ValueError):validate_registry(p,c,'synthetic')


def test_no_network_api_or_clinical_engine():
    import ast
    path=Path(__file__).parents[1]/'ros4hc_synthetic_scenario/fall_risk_evidence.py'
    names=set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node,ast.Import): names.update(a.name.split('.')[0] for a in node.names)
        if isinstance(node,ast.ImportFrom):names.add((node.module or '').split('.')[0])
    assert not names & {'openai','anthropic','requests','urllib','socket','http','subprocess','torch'}


def test_revised_offline_demo_retains_threshold():
    from ros4hc_synthetic_scenario.literature_curation import fall_risk_demo_records
    p,c=fall_risk_demo_records()
    records=convert_registry(p,c,'synthetic')
    assert len(records)==2
    f=records[0]['curation_provenance']['fall_risk']
    assert f['risk_indicator']['threshold_value']==123
    assert f['risk_indicator']['unit']=='synthetic_unit'
    assert f['population']['description']=='Synthetic test population only'
    assert f['results']['auc']==0.75
    assert all(r['synthetic_fixture'] is True for r in records)


def test_not_reported_in_lists_does_not_count_as_coverage(registry):
    p,c=registry
    c[0]['fall_risk']['limitations']['paper_reported_limitations']=['not reported']
    c[0]['fall_risk']['measurement']['sensor_modalities']=['unknown']
    _,c,_=validate_registry(p,c,'synthetic')
    fields=condition_coverage(c)['fields']
    assert fields['limitations']['reported_chunks']==0
    assert fields['sensor_modality']['reported_chunks']==0

"""Offline Phase 5F contracts and synthetic retrieval engineering checks."""
import copy
import json
from pathlib import Path
import pytest
from ros4hc_synthetic_scenario.evidence import generate_query, build_bundle, write_jsonl, read_sensor_inputs
from ros4hc_synthetic_scenario.literature_corpus import load_corpus, validate_corpus, validate_record
from ros4hc_synthetic_scenario.literature_retrieval import LexicalRetrievalBaseline

FIXTURE = Path(__file__).parents[1]/'ros4hc_synthetic_scenario/fixtures/synthetic_literature_corpus.jsonl'
HR = {'hr': {'mean_hr_bpm': 82, 'hr_change_bpm': 8}, 'acc': {}}
ACC = {'hr': {}, 'acc': {'activity_magnitude_mean': 4, 'trend': 'increasing'}}
BOTH = {'hr': HR['hr'], 'acc': ACC['acc']}


def test_valid_fixture():
    corpus = load_corpus(FIXTURE)
    assert len(corpus) == 10
    assert all(r['synthetic_fixture'] is True and 'Synthetic test evidence' in r['evidence_text'] for r in corpus)
    assert all(r['doi'] is None and r['year'] is None for r in corpus)


@pytest.mark.parametrize('key', ['paper_id', 'chunk_id', 'title', 'evidence_text'])
def test_missing_required(key):
    r = load_corpus(FIXTURE)[0]
    del r[key]
    with pytest.raises(ValueError):
        validate_record(r)


@pytest.mark.parametrize('key,value', [('evidence_text',' '), ('year','2026'), ('year',True),
    ('year',0), ('doi','invalid'), ('title',[]), ('authors','name'), ('authors',[1]),
    ('sensor_modalities',{}), ('synthetic_fixture','true'), ('synthetic_fixture',False), ('unexpected',0)])
def test_malformed(key, value):
    r = load_corpus(FIXTURE)[0]
    r[key] = value
    with pytest.raises(ValueError):
        validate_record(r)


def test_duplicates_and_optional_doi():
    r = load_corpus(FIXTURE)[0]
    with pytest.raises(ValueError):
        validate_corpus([r,r])
    r['doi'] = '10.1234/test'
    assert validate_record(r)['doi'] == r['doi']
    del r['doi']
    assert validate_record(r)['doi'] is None


@pytest.mark.parametrize('facts,mods,keyword', [(HR,['hr'],'heart rate'),
    (ACC,['activity'],'physical activity'), (BOTH,['activity','hr'],'multimodal sensing')])
def test_queries(facts, mods, keyword):
    original = copy.deepcopy(facts)
    q = generate_query(facts)
    assert q['modalities'] == mods and keyword in q['keywords']
    assert q['population'] is None and q['clinical_condition'] is None
    assert q == generate_query(facts) and facts == original
    assert not any(word in json.dumps(q) for word in
                   ('tachycardia','bradycardia','abnormal','exercise','fatigue','fall risk','disease'))


@pytest.mark.parametrize('number', [8,175,-100,0])
def test_numeric_separation(number):
    q = generate_query({'hr':{'mean_hr_bpm':number},'acc':{}})
    assert q['observed_values']['mean_hr_bpm'] == number
    assert q['keywords'] == ['heart rate']


@pytest.mark.parametrize('facts', [{}, {'hr':{},'acc':{}},
    {'hr':{'metadata':{'unit':'bpm'}},'acc':{}},
    {'hr':{'mean_hr_bpm':None},'acc':{'trend':'unavailable'}}])
def test_unknown_and_metadata_only(facts):
    q = generate_query(facts)
    assert not q['keywords'] and not q['modalities']
    assert LexicalRetrievalBaseline(load_corpus(FIXTURE)).retrieve(q) == []


@pytest.mark.parametrize('facts,paper', [(HR,'synthetic_paper_002'),
    (ACC,'synthetic_paper_003'), (BOTH,'synthetic_paper_005')])
def test_ranking(facts,paper):
    results = LexicalRetrievalBaseline(load_corpus(FIXTURE)).retrieve(generate_query(facts))
    assert results[0]['paper_id'] == paper
    assert results[0]['retrieval_score'] > 0 and results[0]['matched_terms']


def test_ties_provenance_normalization():
    corpus = load_corpus(FIXTURE)
    r = copy.deepcopy(corpus[0]); r['paper_id'] = 'synthetic_paper_000'
    corpus = [corpus[0],r]
    a = LexicalRetrievalBaseline(corpus).retrieve(generate_query(HR))
    assert a == LexicalRetrievalBaseline(corpus[::-1]).retrieve(generate_query(HR))
    assert a[0]['paper_id'] == r['paper_id']
    assert a[0]['provenance']['title'] == r['title']
    assert a[0]['provenance']['chunk_id'] == r['chunk_id']
    assert len(a[0]['corpus_record_sha256']) == 64
    assert a[0]['synthetic_fixture'] and a[0]['limitations']


@pytest.mark.parametrize('top_k',[0,1,5,100])
def test_bundle(top_k,tmp_path):
    retriever = LexicalRetrievalBaseline(load_corpus(FIXTURE))
    bundle = build_bundle(BOTH,retriever,top_k)
    assert bundle['sensor_facts'] == BOTH and bundle['query'] == generate_query(BOTH)
    assert len(bundle['evidence']) <= top_k
    assert bundle['retrieval_metadata']['method'] == 'lexical_baseline'
    write_jsonl(tmp_path/'a',[bundle]); write_jsonl(tmp_path/'b',[build_bundle(BOTH,retriever,top_k)])
    assert (tmp_path/'a').read_bytes() == (tmp_path/'b').read_bytes()
    bundle['sensor_facts']['hr'].clear()
    assert BOTH['hr']


@pytest.mark.parametrize('key', ['ground_truth_state','state_label_ja','expected_state',
    'perturbation','perturbations','scenario_label','scenario','hidden_future_state',
    'future_numeric_target','split','split_label','future_observations'])
@pytest.mark.parametrize('nested',[False,True])
def test_leakage(key,nested):
    facts = copy.deepcopy(HR)
    target = facts.setdefault('acc',{}).setdefault('metadata',{}).setdefault('quality',{}) if nested else facts
    target[key] = 'opaque'
    with pytest.raises(ValueError):
        build_bundle(facts, LexicalRetrievalBaseline(load_corpus(FIXTURE)))


def test_history_and_envelope(tmp_path):
    facts = {'windows':[dict(relative_minutes=-5,**HR),dict(relative_minutes=0,**ACC)],
             'window_minutes':5,'available_history_windows':1,'available_history_minutes':5}
    q=generate_query(facts)
    assert q['observed_values']['0']['mean_hr_bpm'] == 82
    assert q['observed_values']['1']['activity_magnitude_mean'] == 4
    write_jsonl(tmp_path/'facts',[{'sample_id':'opaque','condition':'b','task_input':facts}])
    assert read_sensor_inputs(tmp_path/'facts') == [facts]


@pytest.mark.parametrize('value', ['82',True,float('nan'),{}])
def test_invalid_numeric(value):
    with pytest.raises(ValueError):
        generate_query({'hr':{'mean_hr_bpm':value}})


@pytest.mark.parametrize('top_k',[-1,True,1.5])
def test_invalid_top_k(top_k):
    with pytest.raises(ValueError):
        build_bundle(HR,LexicalRetrievalBaseline(load_corpus(FIXTURE)),top_k)


def test_no_network_api_dependencies():
    import ast
    root = Path(__file__).parents[1]/'ros4hc_synthetic_scenario'
    forbidden = {'openai','anthropic','google','requests','urllib','socket','http','torch','chromadb'}
    for name in ('evidence','literature_corpus','literature_retrieval'):
        tree = ast.parse((root/(name+'.py')).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                assert not {a.name.split('.')[0] for a in node.names} & forbidden
            if isinstance(node,ast.ImportFrom):
                assert (node.module or '').split('.')[0] not in forbidden


@pytest.mark.parametrize('offsets', [[-5,5],[0,-5],[-5,-5],[1],[None]])
def test_future_or_unordered_history_rejected(offsets):
    with pytest.raises(ValueError):
        generate_query({'windows':[dict(relative_minutes=o,**HR) for o in offsets]})


def test_demo_determinism_and_separation(tmp_path):
    from ros4hc_synthetic_scenario.evidence_eval import run_demo
    config = Path(__file__).parents[1]/'configs/daily_subject_001.json'
    bundles = run_demo(tmp_path/'first',config)
    assert len(bundles) == 4 and bundles[-1]['evidence'] == []
    run_demo(tmp_path/'second',config)
    for path in (tmp_path/'first').iterdir():
        assert path.read_bytes() == (tmp_path/'second'/path.name).read_bytes()
    visible = (tmp_path/'first/evidence_bundles.jsonl').read_text()
    assert all(k not in visible for k in ['ground_truth_state','state_label_ja','expected_state',
                                         'scenario_label','perturbation','future_numeric_target','split_label'])

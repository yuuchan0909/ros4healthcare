"""Conservative structured measurement baseline; no clinical applicability inference."""
import copy
import hashlib
import json
from .fall_risk_evidence import known
from .literature_corpus import validate_corpus
from .literature_retrieval import LexicalRetrievalBaseline

FAMILIES = (
    ('hr', 'heart rate', 'heart-rate'),
    ('activity', 'accelerometer', 'accelerometers', 'accelerometry', 'imu',
     'inertial measurement unit', 'inertial measurement units', 'inertial sensor', 'inertial sensors'),
    ('camera', 'image', 'images', 'home images', 'visual input', 'vision-language input',
     'multimodal vision-language input'),
)


def normalized(value):
    return ' '.join(value.casefold().split())


def family(value):
    value = normalized(value)
    return next((group[0] for group in FAMILIES if value in group), value)


def applicability(query, record):
    context = (record.get('curation_provenance') or {}).get('fall_risk') or {}
    measurement = context.get('measurement') or {}
    modalities = sorted(set(query['modalities']))
    variables = sorted(set(query['variables']))
    record_modalities = {family(v) for v in measurement.get('sensor_modalities') or [] if known(v)}
    record_variables = {normalized(v) for v in measurement.get('measured_variables') or [] if known(v)}
    supported = [v for v in modalities if known(v) and family(v) in record_modalities]
    unsupported = [v for v in modalities if v not in supported]
    matched = [v for v in variables if known(v) and normalized(v) in record_variables]
    unmatched = [v for v in variables if v not in matched]
    if modalities and record_modalities and not supported:
        status, reason = 'mismatch', 'No explicitly corresponding modality family'
    elif not modalities or not record_modalities:
        status, reason = 'unknown', 'Query or structured record modality unavailable'
    elif unsupported or unmatched or not variables or not record_variables:
        status, reason = 'partial', 'Modality support is incomplete or direct variable correspondence is incomplete/unconfirmed'
    else:
        status, reason = 'match', 'All query modalities and variables explicitly correspond; protocol equivalence is unassessed'
    outcome = copy.deepcopy(context.get('fall_outcome') or {})
    # Exact, explicit curated declarations only; no general natural-language inference.
    no_outcome = outcome.get('ascertainment_method') in (
        'No fall-event outcome was measured; the task was environmental hazard identification and recommendation generation.',
        'No prospective or retrospective fall outcome was measured.',
    )
    documented = (known(outcome.get('fall_definition')) and
                  outcome.get('prospective_or_retrospective') in ('prospective', 'retrospective', 'mixed') and
                  known(outcome.get('ascertainment_method')))
    outcome_status = 'no_fall_outcome' if no_outcome else 'documented' if documented else 'unknown'
    fields = ('fall_definition', 'prospective_or_retrospective', 'followup_duration', 'ascertainment_method')
    unknown = ['population', 'clinical_condition', 'measurement.protocol_equivalence',
               'measurement.test_name_equivalence', 'measurement.body_location_equivalence']
    unknown += ['fall_outcome.' + k for k in fields if not known(outcome.get(k))]
    if not record_modalities:
        unknown.append('measurement.sensor_modalities')
    if not record_variables:
        unknown.append('measurement.measured_variables')
    return {
        'measurement': dict(supported_query_modalities=supported, unsupported_query_modalities=unsupported,
                            matched_variables=matched, unmatched_variables=unmatched,
                            measurement_status=status, measurement_reasons=[reason],
                            record_conditions={k: copy.deepcopy(measurement.get(k)) for k in
                                               ('sensor_modalities', 'measured_variables', 'protocol', 'test_name', 'body_location')}),
        'population': {'population_status': 'unknown',
                       'reason': 'Query population absent' if query.get('population') is None else 'Structured population comparison not implemented',
                       'record_conditions': copy.deepcopy(context.get('population'))},
        'fall_outcome': {'fall_outcome_status': outcome_status,
                         'record_conditions': {k: outcome.get(k) for k in fields},
                         'field_present': {k: known(outcome.get(k)) for k in fields}},
        'overall_status': {'match': 'compatible', 'partial': 'partial', 'mismatch': 'mismatch', 'unknown': 'unknown'}[status],
        'unknown_conditions': unknown,
    }


class ConditionAwareRetrievalBaseline:
    """Candidate union: lexical signal or structured modality support.

    Final evidence requires structured modality support; audit() retains every record.
    Overall describes measurement correspondence only, never person suitability.
    """
    method = 'conservative_condition_aware_baseline'

    def __init__(self, corpus):
        self.corpus = validate_corpus(corpus)

    def audit(self, query):
        lexical = {(r['paper_id'], r['chunk_id']): r for r in
                   LexicalRetrievalBaseline(self.corpus).retrieve(query, len(self.corpus))}
        rows = []
        for record in self.corpus:
            identity = (record['paper_id'], record['chunk_id'])
            app = applicability(query, record)
            m = app['measurement']
            signal = lexical.get(identity, {}).get('retrieval_score', 0)
            supported = len(m['supported_query_modalities'])
            candidate = bool(signal or supported)
            selected = candidate and supported > 0 and app['overall_status'] in ('compatible', 'partial')
            rows.append(dict(paper_id=identity[0], chunk_id=identity[1], applicability=app,
                             lexical_signal=signal, candidate=candidate, eligible_for_evidence=selected,
                             ranking_key=[{'compatible': 0, 'partial': 1, 'unknown': 2, 'mismatch': 3}[app['overall_status']],
                                          -supported, -len(m['matched_variables']), -signal, *identity]))
        return sorted(rows, key=lambda r: r['ranking_key'])

    def retrieve(self, query, top_k=5):
        if type(top_k) is not int or top_k < 0:
            raise ValueError('top_k must be a nonnegative integer')
        records = {(r['paper_id'], r['chunk_id']): r for r in self.corpus}
        results = []
        for row in self.audit(query):
            if not row['eligible_for_evidence']:
                continue
            r = records[row['paper_id'], row['chunk_id']]
            provenance = {k: copy.deepcopy(r[k]) for k in ('paper_id', 'chunk_id', 'title', 'authors', 'year', 'doi', 'url')}
            if 'curation_provenance' in r:
                provenance['curation'] = copy.deepcopy(r['curation_provenance'])
            results.append(dict(paper_id=r['paper_id'], chunk_id=r['chunk_id'], title=r['title'], provenance=provenance,
                                corpus_record_sha256=hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                                evidence_text=r['evidence_text'], synthetic_fixture=r['synthetic_fixture'],
                                applicability=row['applicability'], ranking_key=row['ranking_key'], lexical_signal=row['lexical_signal'],
                                limitations=['Structured measurement correspondence only; no clinical applicability or quality assessment'] +
                                (['Synthetic fixture; not empirical evidence'] if r['synthetic_fixture'] else [])))
        return results[:top_k]

    def build_bundle(self, facts, top_k=5):
        from .evidence import generate_query
        query = generate_query(facts)
        return dict(query=query, sensor_facts=copy.deepcopy(facts), evidence=self.retrieve(query, top_k),
                    retrieval_metadata=dict(method=self.method, top_k=top_k, corpus_record_count=len(self.corpus),
                                            audit=self.audit(query)))

"""Curator-reported applicability conditions, not individual risk predictions."""
import copy
import math
from collections import Counter
from typing import TypedDict

CATEGORIES = ('motor_function', 'fall_history', 'medication', 'environment',
              'psychological', 'physiological', 'other', 'unknown')
OPERATORS = ('<', '<=', '=', '>=', '>')
# Explicit field/type contract; null is accepted everywhere. No text-to-number inference.
SCHEMA = {
    'population': {'description':'text', 'sample_size':'integer', 'age':'text', 'setting':'text',
                   'inclusion_criteria':'list', 'exclusion_criteria':'list'},
    'measurement': {'sensor_modalities':'list', 'devices':'list', 'protocol':'text',
                    'measured_variables':'list', 'test_name':'text',
                    'measurement_distance_or_duration':'text', 'body_location':'list'},
    'risk_indicator': {'indicator_name':'text', 'threshold_value':'number',
                       'threshold_operator':'operator', 'unit':'text', 'unit_required':'boolean',
                       'reference_criterion':'text'},
    'fall_outcome': {'fall_definition':'text', 'ascertainment_method':'text',
                     'prospective_or_retrospective':'ascertainment', 'followup_duration':'text'},
    'results': {'result_summary':'text', 'auc':'proportion', 'sensitivity':'proportion',
                'specificity':'proportion', 'effect_size':'effect', 'confidence_interval':'interval',
                'p_value':'proportion', 'p_value_operator':'operator', 'reporting_notes':'text'},
    'limitations': {'paper_reported_limitations':'list'},
    'knowledge_base': {'risk_factor_category':'category', 'evidence_strength_note':'text'},
    'provenance': {'section':'text', 'page':'text', 'table':'text', 'figure':'text', 'doi':'doi', 'url':'text'},
}


class FallRiskEvidenceRecord(TypedDict):
    """Optional EvidenceChunk.fall_risk payload; identity belongs to its parent chunk."""
    population: dict
    measurement: dict
    risk_indicator: dict
    fall_outcome: dict
    results: dict
    limitations: dict
    knowledge_base: dict
    provenance: dict
    value_origin: str | None


def known(value):
    if isinstance(value, list):
        return any(known(item) for item in value)
    if isinstance(value, str):
        return value.strip().casefold() not in ('', 'unknown', 'not reported', 'not_reported', '不明', '未報告')
    return value is not None and value != []


def _value(value, kind, path):
    if value is None:
        return None
    valid = True
    if kind in ('number','proportion','integer'):
        valid = type(value) in (int,float) and math.isfinite(value)
        if kind == 'integer': valid = type(value) is int and value >= 1
        if kind == 'proportion': valid = valid and 0 <= value <= 1
    elif kind == 'boolean': valid = type(value) is bool
    elif kind == 'list':
        valid = isinstance(value,list) and all(isinstance(v,str) and v.strip() for v in value)
    elif kind in ('effect','interval'):
        fields = {'value':'number','measure':'text','unit':'text'} if kind == 'effect' else {
            'lower':'number','upper':'number','confidence_level':'proportion','measure':'text','unit':'text'}
        if not isinstance(value,dict) or set(value)-set(fields):
            raise ValueError('Unsupported structured result: '+path)
        out = {k:_value(value.get(k),typ,path+'.'+k) for k,typ in fields.items()}
        if kind == 'interval' and out['lower'] is not None and out['upper'] is not None and out['lower'] > out['upper']:
            raise ValueError('Reversed confidence interval: '+path)
        return out
    else:
        valid = isinstance(value,str)
        enums = {'operator':OPERATORS, 'category':CATEGORIES,
                 'ascertainment':('prospective','retrospective','mixed','unknown','not_reported')}
        if kind in enums: valid = valid and value in enums[kind]
        if kind == 'doi':
            from .literature_registry import normalize_doi
            return normalize_doi(value)
    if not valid:
        raise ValueError('Invalid fall-risk field: '+path)
    return copy.deepcopy(value)


def normalize_fall_risk(record):
    if not isinstance(record,dict) or set(record)-(set(SCHEMA)|{'value_origin'}):
        raise ValueError('Unsupported fall-risk fields')
    origin = record.get('value_origin')
    if origin not in (None,'paper_reported','not_reported','unknown'):
        raise ValueError('Inferred values are not supported; curate only paper-reported information')
    out = {'value_origin':origin}
    for group, fields in SCHEMA.items():
        value = record.get(group)
        if value is None: value = {}
        if not isinstance(value,dict) or set(value)-set(fields):
            raise ValueError('Unsupported fall-risk group fields: '+group)
        out[group] = {k:_value(value.get(k),typ,group+'.'+k) for k,typ in fields.items()}
    indicator = out['risk_indicator']
    if indicator['threshold_value'] is not None and indicator['unit_required'] is True and not known(indicator['unit']):
        raise ValueError('Threshold requires its paper-reported unit')
    return out


def has_location(chunk):
    structured = (chunk.get('fall_risk') or {}).get('provenance') or {}
    return any(known(chunk.get(k)) for k in ('section','page','figure_or_table')) or any(
        known(structured.get(k)) for k in ('section','page','table','figure'))


def condition_warnings(chunk):
    f = chunk.get('fall_risk')
    identity = [chunk['paper_id'],chunk['chunk_id']]
    if f is None:
        return [{'code':'missing_fall_risk_conditions','identity':identity}]
    warnings = []
    def warn(code): warnings.append({'code':code,'identity':identity})
    checks = [('missing_population',f['population']['description']),
              ('missing_measurement_protocol',f['measurement']['protocol']),
              ('missing_fall_definition',f['fall_outcome']['fall_definition']),
              ('missing_followup',f['fall_outcome']['followup_duration'])]
    missing = [code for code,value in checks if not known(value)]
    for code in missing: warn(code)
    indicator = f['risk_indicator']
    if indicator['threshold_value'] is not None:
        if missing: warn('threshold_without_applicability_metadata')
        if not known(indicator['unit']): warn('threshold_unit_not_reported')
        if indicator['threshold_operator'] is None: warn('threshold_operator_not_reported')
        if not any(_present(chunk,'results',k) for k in ('auc','sensitivity','specificity','effect_size')):
            warn('threshold_without_reported_performance')
        if not known(f['fall_outcome']['prospective_or_retrospective']):
            warn('threshold_ascertainment_not_reported')
    if any(_present(chunk,'results',k) for k in f['results']) and not has_location(chunk):
        warn('result_without_source_location')
    if f['value_origin'] is None: warn('value_origin_not_recorded')
    return warnings


def condition_coverage(chunks):
    fields = {
        'population':('population','description'), 'sensor_modality':('measurement','sensor_modalities'),
        'measurement_variable':('measurement','measured_variables'), 'measurement_protocol':('measurement','protocol'),
        'threshold':('risk_indicator','threshold_value'), 'fall_definition':('fall_outcome','fall_definition'),
        'followup':('fall_outcome','followup_duration'), 'risk_factor_category':('knowledge_base','risk_factor_category'),
        'limitations':('limitations','paper_reported_limitations'),
        'evidence_strength_note':('knowledge_base','evidence_strength_note')}
    for metric in ('auc','sensitivity','specificity','effect_size','confidence_interval','p_value'):
        fields[metric] = ('results',metric)
    result = {'chunk_count':len(chunks), 'structured_record_count':sum(c.get('fall_risk') is not None for c in chunks),
              'fields':{}}
    for label,(group,key) in fields.items():
        values=[]
        for c in chunks:
            f=c.get('fall_risk') or {}; value=(f.get(group) or {}).get(key)
            if isinstance(value,dict):
                numeric_keys = ('value',) if key == 'effect_size' else ('lower','upper')
                value = 'reported' if any(value.get(k) is not None for k in numeric_keys) else None
            if known(value): values.extend(sorted({v for v in value if known(v)}) if isinstance(value,list) else [str(value)])
        present = sum(1 for c in chunks if _present(c,group,key))
        result['fields'][label]={'reported_chunks':present,'unknown_or_missing_chunks':len(chunks)-present,
                                'values':dict(sorted(Counter(values).items()))}
    result['provenance']={'located_chunks':sum(has_location(c) for c in chunks),
                          'verified_location_chunks':sum(c['source_location_verified'] for c in chunks)}
    return result


def _present(chunk,group,key):
    value=((chunk.get('fall_risk') or {}).get(group) or {}).get(key)
    if isinstance(value,dict):
        return any(value.get(k) is not None for k in (('value',) if key=='effect_size' else ('lower','upper')))
    return known(value)

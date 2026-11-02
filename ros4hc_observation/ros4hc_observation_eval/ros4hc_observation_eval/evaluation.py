"""Factual evaluation of v0.2 models or generated messages; no ROS imports."""
import json
import math
from numbers import Real

from ros4hc_observation_tools.model import DataLevel, TimeSemantics, UnitStatus, Validity
from ros4hc_observation_tools.model_v02 import (
    validate_reference, validate_scalar, validate_vector,
)
from ros4hc_observation_tools.in_memory_reference import ReferenceResolutionError


def representation(observation):
    if hasattr(observation, 'reference'):
        return 'reference'
    if hasattr(observation, 'value'):
        return 'vector' if all(hasattr(observation.value, a) for a in 'xyz') else 'scalar'
    raise TypeError('Expected a Scalar, Vector, or ReferenceObservation')


def metadata_facts(observation):
    """Extract values, including blanks and enum codes, without scoring them."""
    m = observation.metadata
    t, s, q = m.observation_time, m.source, m.quality
    return {
        'representation': representation(observation),
        'observation_id': m.observation_id,
        'observation_id_present': bool(m.observation_id.strip()),
        'subject_id': m.subject_id, 'subject_id_present': bool(m.subject_id.strip()),
        'session_id': m.session_id, 'session_id_present': bool(m.session_id.strip()),
        'observation_type': m.observation_type, 'unit': m.unit,
        'unit_status': int(m.unit_status),
        'primary_time_semantics': int(t.semantics),
        'primary_time_stamp': {'sec': t.stamp.sec, 'nanosec': t.stamp.nanosec},
        'clock_domain': t.clock_domain, 'event_source': t.event_source,
        'source': {name: getattr(s, name) for name in
                   ('device_type', 'device_id', 'topic', 'message_type', 'field_path', 'node')},
        'data_level': int(m.data_level), 'validity': int(q.validity),
        'quality_available': q.quality_available,
        'quality_value': q.quality_value, 'quality_scale': q.quality_scale,
    }


def metadata_completeness(observation):
    """Field states, not a percentage or requirement to populate every field."""
    facts = metadata_facts(observation)
    states = {}
    for name in ('observation_id', 'subject_id', 'session_id', 'observation_type',
                 'unit', 'clock_domain', 'event_source'):
        states[name] = 'present' if facts[name].strip() else 'empty'
    for name, value in facts['source'].items():
        states['source.' + name] = 'present' if value.strip() else 'empty'
    enums = {'unit_status': UnitStatus, 'primary_time_semantics': TimeSemantics,
             'data_level': DataLevel, 'validity': Validity}
    for name, enum in enums.items():
        states[name] = ('UNKNOWN' if facts[name] == enum.UNKNOWN else
                        'present' if facts[name] in enum._value2member_map_ else 'unrecognized')
    states['quality_available'] = 'present' if facts['quality_available'] else 'unavailable'
    if facts['quality_available']:
        states['quality_value'] = 'present'
        states['quality_scale'] = 'present' if facts['quality_scale'].strip() else 'empty'
    else:
        states['quality_value'] = ('unavailable' if math.isnan(facts['quality_value'])
                                   else 'inconsistent')
        states['quality_scale'] = ('unavailable' if facts['quality_scale'] == ''
                                   else 'inconsistent')
    return states


def unknown_semantics(observation):
    """Observed semantic states; these flags are not failures or inferred truth."""
    m, q = observation.metadata, observation.metadata.quality
    result = {
        'unit_status_unknown': m.unit_status == UnitStatus.UNKNOWN,
        'unit_empty': m.unit == '', 'validity_unknown': q.validity == Validity.UNKNOWN,
        'quality_unavailable': not q.quality_available,
        'quality_value_is_nan': math.isnan(q.quality_value),
        'quality_scale_empty': q.quality_scale == '',
        'unavailable_quality_contract_satisfied': (
            not q.quality_available and math.isnan(q.quality_value) and q.quality_scale == ''
        ) if not q.quality_available else None,
    }
    if representation(observation) == 'vector':
        result['frame_id_empty'] = observation.frame_id == ''
    return result


def compare_numeric(expected, actual):
    """Exact numeric equality, paired NaNs preserved; infinity always fails."""
    numeric = all(isinstance(v, Real) and not isinstance(v, bool) for v in (expected, actual))
    if not numeric:
        exact, reason = False, 'non_numeric'
    elif math.isinf(expected) or math.isinf(actual):
        exact, reason = False, 'infinity_forbidden'
    elif math.isnan(expected) or math.isnan(actual):
        exact = math.isnan(expected) and math.isnan(actual)
        reason = 'nan_preserved' if exact else 'nan_mismatch'
    else:
        exact = expected == actual
        reason = 'finite_exact' if exact else 'finite_mismatch'
    return {'expected': expected, 'actual': actual, 'exact': exact,
            'pass': exact, 'reason': reason}


def evaluate_scalar(observation, expected):
    if representation(observation) != 'scalar':
        raise TypeError('Expected ScalarObservation')
    comparison = compare_numeric(expected, observation.value)
    errors = validate_scalar(observation)
    return {**comparison, 'validation_errors': errors,
            'pass': comparison['pass'] and not errors}


def evaluate_vector(observation, expected, expected_frame_id):
    if representation(observation) != 'vector':
        raise TypeError('Expected VectorObservation')
    components = {axis: compare_numeric(getattr(expected, axis), getattr(observation.value, axis))
                  for axis in 'xyz'}
    frame = {'expected': expected_frame_id, 'actual': observation.frame_id,
             'exact': expected_frame_id == observation.frame_id}
    exact = all(c['exact'] for c in components.values()) and frame['exact']
    errors = validate_vector(observation)
    return {'components': components, 'frame_id': frame, 'exact': exact,
            'validation_errors': errors, 'pass': exact and not errors}


def reference_descriptor(observation):
    return {name: getattr(observation.reference, name) for name in ('scheme', 'id', 'profile')}


def _native_fields(value):
    """Walk generated native messages, including nested Time and complete arrays."""
    if hasattr(value, 'get_fields_and_field_types'):
        return {name: _native_fields(getattr(value, name))
                for name in value.get_fields_and_field_types()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return [_native_fields(item) for item in value]


def evaluate_reference(observation, expected_native, resolver):
    """Caller must supply the registering resolver instance in this process.

    Descriptor/metadata validation remains separate from resolution. No resolver
    is created here. Failures do not alter measurement quality or validity.
    """
    result = {'scope': 'same_process_same_resolver_instance_only',
              'cross_process_evaluated': False,
              'descriptor': reference_descriptor(observation),
              'validation_errors': validate_reference(observation)}
    try:
        recovered = resolver.resolve(observation.reference, observation.metadata.source.message_type)
    except ReferenceResolutionError as error:
        return {**result, 'resolved': False, 'pass': False,
                'failure_code': error.code.value, 'failure_detail': str(error)}
    expected, actual = _native_fields(expected_native), _native_fields(recovered)
    fields = {name: {'exact': name in actual and value == actual[name]}
              for name, value in expected.items()}
    exact = type(recovered) is type(expected_native) and expected == actual
    return {**result, 'resolved': True, 'native_type_preserved': type(recovered) is type(expected_native),
            'fields': fields, 'exact': exact,
            'pass': exact and not result['validation_errors']}


def evaluation_report(observation):
    """Base report; unevaluated comparisons are explicit rather than implied passes."""
    kind = representation(observation)
    validator = {'scalar': validate_scalar, 'vector': validate_vector, 'reference': validate_reference}[kind]
    result = {'representation': kind, 'metadata': metadata_facts(observation),
              'metadata_completeness': metadata_completeness(observation),
              'validation_errors': validator(observation),
              'unknown_semantics': unknown_semantics(observation),
              'value_preservation': {'evaluated': False},
              'reference_recovery': {'evaluated': False}}
    if kind == 'reference':
        result['reference_descriptor'] = reference_descriptor(observation)
    return result


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return 'NaN' if math.isnan(value) else 'Infinity' if value > 0 else '-Infinity'
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def report_json(report):
    """Strict JSON; non-finite numbers use documented string tokens."""
    return json.dumps(_json_safe(report), indent=2, allow_nan=False, sort_keys=True)

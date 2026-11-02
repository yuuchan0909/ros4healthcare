"""Focused unittest contracts; no hardware or running ROS graph."""
from copy import deepcopy
from dataclasses import replace
import json
import math
from types import SimpleNamespace as NS
import unittest

from ros4hc_observation_tools.model import Validity
from ros4hc_observation_tools.model_v02 import (
    ObservationMetadata, ObservationSource, ScalarObservation, Vector3, VectorObservation,
    ReferenceObservation, ReferenceDescriptor, validate_scalar, validate_vector,
)
from ros4hc_observation_tools.in_memory_reference import InMemoryReferenceResolver
from ros4hc_observation_eval.evaluation import (
    compare_numeric, metadata_facts, metadata_completeness, unknown_semantics,
    evaluate_scalar, evaluate_vector, evaluate_reference, evaluation_report, report_json,
)
from ros4hc_observation_eval.demo import demo_report, synthetic_raw, sensomative_case


def metadata():
    return ObservationMetadata('observation', 'subject', 'study-session', 'synthetic',
        source=ObservationSource(device_type='synthetic', topic='/synthetic',
                                 message_type='example/msg/Native', field_path='$'))


class MetadataTest(unittest.TestCase):
    def test_common_extraction_all_representations(self):
        m = metadata()
        for value, kind in [(ScalarObservation(m, 1.0), 'scalar'),
                            (VectorObservation(m, Vector3(1.0, 2.0, 3.0)), 'vector'),
                            (ReferenceObservation(m, ReferenceDescriptor('s', 'id', 'p')), 'reference')]:
            with self.subTest(kind=kind):
                facts = metadata_facts(value)
                self.assertEqual(facts['representation'], kind)
                self.assertTrue(facts['observation_id_present'])
                self.assertTrue(facts['subject_id_present'])
                self.assertTrue(facts['session_id_present'])
                self.assertEqual(facts['source']['message_type'], 'example/msg/Native')
                self.assertEqual(facts['source']['field_path'], '$')
                self.assertEqual(facts['primary_time_stamp'], {'sec': 0, 'nanosec': 0})
                self.assertEqual(facts['clock_domain'], '')
                self.assertEqual(facts['event_source'], '')

    def test_unknown_unavailable_are_legitimate(self):
        value = VectorObservation(metadata(), Vector3(1.0, math.nan, 3.0))
        report = evaluation_report(value)
        self.assertEqual(report['validation_errors'], [])
        states = report['metadata_completeness']
        self.assertEqual(states['observation_id'], 'present')
        self.assertEqual(states['unit'], 'empty')
        for key in ('unit_status', 'validity', 'primary_time_semantics', 'data_level'):
            self.assertEqual(states[key], 'UNKNOWN')
        self.assertEqual(states['quality_value'], 'unavailable')
        self.assertEqual(states['quality_scale'], 'unavailable')
        self.assertTrue(all(report['unknown_semantics'][key] for key in (
            'unit_status_unknown', 'unit_empty', 'validity_unknown', 'quality_unavailable',
            'quality_value_is_nan', 'quality_scale_empty', 'frame_id_empty',
            'unavailable_quality_contract_satisfied')))

    def test_empty_required_identity_reported_without_repair(self):
        value = ScalarObservation(replace(metadata(), subject_id=''), 1.0)
        self.assertFalse(metadata_facts(value)['subject_id_present'])
        self.assertEqual(metadata_completeness(value)['subject_id'], 'empty')
        self.assertTrue(evaluation_report(value)['validation_errors'])
        self.assertEqual(value.metadata.subject_id, '')

    def test_inconsistent_unavailable_quality_is_not_hidden(self):
        m = metadata()
        for number in (0.9, math.inf, -math.inf):
            with self.subTest(number=number):
                value = ScalarObservation(replace(m, quality=replace(m.quality, quality_value=number)), 1.0)
                self.assertEqual(metadata_completeness(value)['quality_value'], 'inconsistent')
                self.assertEqual(metadata_completeness(value)['quality_scale'], 'unavailable')
                self.assertFalse(unknown_semantics(value)['unavailable_quality_contract_satisfied'])
                self.assertEqual(evaluation_report(value)['validation_errors'], validate_scalar(value))
                self.assertTrue(validate_scalar(value))

    def test_unavailable_quality_nonempty_scale_is_inconsistent(self):
        m = metadata()
        for scale in ('score', ' '):
            with self.subTest(scale=scale):
                value = ScalarObservation(replace(m, quality=replace(m.quality, quality_scale=scale)), 1.0)
                states = metadata_completeness(value)
                self.assertEqual(states['quality_value'], 'unavailable')
                self.assertEqual(states['quality_scale'], 'inconsistent')
                self.assertEqual(evaluation_report(value)['validation_errors'], validate_scalar(value))
                self.assertTrue(validate_scalar(value))

    def test_available_quality_remains_factual_and_validator_controls_validity(self):
        m = metadata()
        for number, scale in ((0.9, 'score'), (math.nan, ''), (math.inf, ' ')):
            with self.subTest(number=number, scale=scale):
                q = replace(m.quality, quality_available=True, quality_value=number, quality_scale=scale)
                value = ScalarObservation(replace(m, quality=q), 1.0)
                states = metadata_completeness(value)
                self.assertEqual(states['quality_value'], 'present')
                self.assertEqual(states['quality_scale'], 'present' if scale.strip() else 'empty')
                self.assertEqual(evaluation_report(value)['validation_errors'], validate_scalar(value))
                self.assertEqual(bool(validate_scalar(value)), not math.isfinite(number) or not scale.strip())

    def test_descriptor_and_unevaluated_sections(self):
        o = ReferenceObservation(metadata(), ReferenceDescriptor('scheme/v1', 'opaque', 'profile/v1'))
        report = evaluation_report(o)
        self.assertEqual(report['reference_descriptor'],
                         {'scheme': 'scheme/v1', 'id': 'opaque', 'profile': 'profile/v1'})
        self.assertFalse(report['value_preservation']['evaluated'])
        self.assertFalse(report['reference_recovery']['evaluated'])

    def test_strict_json_handles_nonfinite(self):
        encoded = report_json({'nan': math.nan, 'pos': math.inf, 'neg': -math.inf})
        self.assertEqual(json.loads(encoded), {'nan': 'NaN', 'pos': 'Infinity', 'neg': '-Infinity'})


class ValuesTest(unittest.TestCase):
    def test_scalar_finite_exact_and_mismatch(self):
        value = ScalarObservation(metadata(), 72.0)
        self.assertTrue(evaluate_scalar(value, 72.0)['pass'])
        self.assertFalse(evaluate_scalar(value, 72.1)['exact'])

    def test_scalar_nan_match_and_mismatch(self):
        value = ScalarObservation(metadata(), math.nan)
        self.assertTrue(evaluate_scalar(value, math.nan)['pass'])
        self.assertEqual(evaluate_scalar(value, math.nan)['reason'], 'nan_preserved')
        self.assertFalse(evaluate_scalar(value, 0.0)['pass'])
        self.assertFalse(compare_numeric(math.nan, 0.0)['pass'])

    def test_infinities_never_pass(self):
        for number in (math.inf, -math.inf):
            value = ScalarObservation(metadata(), number)
            self.assertFalse(evaluate_scalar(value, number)['pass'])
            self.assertFalse(compare_numeric(number, number)['exact'])

    def test_scalar_validator_is_source_of_truth(self):
        m = metadata()
        for validity in Validity:
            for number in (1.0, math.nan, math.inf):
                value = ScalarObservation(replace(m, quality=replace(m.quality, validity=validity)), number)
                report = evaluate_scalar(value, number)
                self.assertEqual(report['validation_errors'], validate_scalar(value))
                self.assertEqual(report['pass'], report['exact'] and not validate_scalar(value))

    def test_vector_components_and_frame(self):
        v = Vector3(0.1, -0.05, 0.98)
        value = VectorObservation(metadata(), v, '')
        report = evaluate_vector(value, v, '')
        self.assertTrue(report['pass'])
        self.assertTrue(all(x['exact'] for x in report['components'].values()))
        self.assertFalse(evaluate_vector(value, v, 'invented-frame')['pass'])
        mismatch = evaluate_vector(value, replace(v, y=0.0), '')
        self.assertTrue(mismatch['components']['x']['exact'])
        self.assertFalse(mismatch['components']['y']['exact'])
        self.assertFalse(mismatch['pass'])

    def test_vector_nonfinite_matches_validator(self):
        m = metadata()
        for validity in Validity:
            for v in (Vector3(1.0, 2.0, 3.0), Vector3(math.nan, math.nan, math.nan),
                      Vector3(1.0, math.nan, 3.0), Vector3(math.inf, 1.0, 2.0)):
                value = VectorObservation(replace(m, quality=replace(m.quality, validity=validity)), v)
                report = evaluate_vector(value, v, '')
                self.assertEqual(report['validation_errors'], validate_vector(value))
                self.assertEqual(report['pass'], report['exact'] and not validate_vector(value))

    def test_non_numeric_does_not_pass_numeric_comparison(self):
        self.assertFalse(compare_numeric(True, 1.0)['pass'])
        self.assertFalse(compare_numeric('1', 1.0)['pass'])


class NativeReferenceTest(unittest.TestCase):
    def setUp(self):
        from sensomative_raw_bridge.reference_mapping import ReferenceConfig, map_reference
        self.raw = synthetic_raw()
        self.resolver = InMemoryReferenceResolver()
        self.observation = map_reference(self.raw, ReferenceConfig('subject', 'study-session'),
                                         '/synthetic/raw', self.resolver)

    def test_all_native_fields_preserved(self):
        result = evaluate_reference(self.observation, self.raw, self.resolver)
        self.assertTrue(result['pass'])
        self.assertEqual(set(result['fields']), set(self.raw.get_fields_and_field_types()))
        self.assertTrue(all(field['exact'] for field in result['fields'].values()))
        self.assertEqual(result['scope'], 'same_process_same_resolver_instance_only')
        self.assertFalse(result['cross_process_evaluated'])
        self.assertNotEqual(self.observation.metadata.session_id, self.raw.session_id)
        facts = metadata_facts(self.observation)
        self.assertEqual(facts['primary_time_semantics'], 2)
        self.assertEqual(facts['clock_domain'], 'unix_utc')
        self.assertEqual(facts['event_source'], 'windows_ble_callback')

    def test_every_native_field_mismatch_is_detected(self):
        for name in self.raw.get_fields_and_field_types():
            changed = deepcopy(self.raw)
            if name == 'ros_publish_time':
                changed.ros_publish_time.nanosec += 1
            elif name == 'payload':
                changed.payload[0] = 255
            elif isinstance(getattr(changed, name), str):
                setattr(changed, name, getattr(changed, name) + '-changed')
            else:
                setattr(changed, name, getattr(changed, name) + 1)
            result = evaluate_reference(self.observation, changed, self.resolver)
            self.assertFalse(result['pass'], name)
            self.assertFalse(result['fields'][name]['exact'], name)

    def test_separate_resolver_is_not_recovery(self):
        result = evaluate_reference(self.observation, self.raw, InMemoryReferenceResolver())
        self.assertFalse(result['pass'])
        self.assertFalse(result['resolved'])
        self.assertEqual(result['failure_code'], 'not_found')

    def test_isolation_and_input_immutability(self):
        expected = deepcopy(self.raw)
        self.raw.payload[0] = 255
        recovered = self.resolver.resolve(self.observation.reference,
                                          self.observation.metadata.source.message_type)
        recovered.payload[1] = 255
        self.assertTrue(evaluate_reference(self.observation, expected, self.resolver)['pass'])
        self.assertEqual(expected, synthetic_raw())

    def test_generated_scalar_and_vector_messages(self):
        from ros4hc_observation_msgs.msg import ScalarObservation as RosScalar, VectorObservation as RosVector
        from ros4hc_observation_tools.v02_bridge_mapping import to_scalar_message
        from ros4hc_observation_tools.polar_acc_mapping import to_vector_message
        scalar = to_scalar_message(ScalarObservation(metadata(), 72.0), RosScalar)
        vector = to_vector_message(VectorObservation(metadata(), Vector3(1.0, 2.0, 3.0)), RosVector)
        self.assertTrue(evaluate_scalar(scalar, 72.0)['pass'])
        self.assertTrue(evaluate_vector(vector, NS(x=1.0, y=2.0, z=3.0), '')['pass'])
        self.assertEqual(metadata_facts(scalar)['source'], metadata_facts(vector)['source'])

    def test_demo_actual_mappings_and_strict_json(self):
        report = json.loads(report_json(demo_report()))
        self.assertTrue(report['synthetic_only'])
        scalar, vector, reference = report['cases']
        self.assertTrue(scalar['value_preservation']['pass'])
        self.assertTrue(vector['value_preservation']['pass'])
        self.assertEqual(vector['metadata']['unit'], 'g')
        self.assertEqual(vector['metadata']['unit_status'], 2)  # Existing mapper's INFERRED.
        self.assertTrue(reference['reference_recovery']['pass'])
        self.assertEqual(reference['metadata']['quality_value'], 'NaN')
        self.assertTrue(sensomative_case()['reference_recovery']['pass'])


if __name__ == '__main__':
    unittest.main()

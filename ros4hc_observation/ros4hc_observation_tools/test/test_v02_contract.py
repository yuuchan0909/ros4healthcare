"""v0.2 contract tests and future mappings, without implementing adapters."""

from dataclasses import fields, replace
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

from ros4hc_observation_tools.model_v02 import (
    DataLevel, ObservationMetadata, ObservationQuality, ObservationSource,
    ObservationTime, ScalarObservation, Stamp, TimeSemantics, UnitStatus,
    Validity, Vector3, VectorObservation, validate_metadata, validate_quality,
    validate_scalar, validate_time, validate_vector,
)


def metadata(**changes):
    return replace(ObservationMetadata(
        observation_id='session-a:1', subject_id='subject-a', session_id='session-a',
        observation_type='unknown_quantity', source=ObservationSource(
            topic='/input', message_type='example_msgs/msg/Sample', field_path='value',
        ),
    ), **changes)


class MetadataContractTest(unittest.TestCase):
    def test_unknown_metadata_and_opaque_ids(self):
        for identifier in ('1', 'session + counter 42', 'recorder:arbitrary-id'):
            self.assertEqual(validate_metadata(metadata(observation_id=identifier)), [])

    def test_required_identifiers_and_source(self):
        for name in ('observation_id', 'subject_id', 'session_id', 'observation_type'):
            for empty in ('', '  '):
                with self.subTest(name=name, empty=empty):
                    self.assertTrue(validate_metadata(metadata(**{name: empty})))
        for name in ('device_type', 'topic', 'message_type', 'field_path'):
            with self.subTest(source=name):
                self.assertTrue(validate_metadata(metadata(
                    source=replace(metadata().source, **{name: ''}),
                )))
        self.assertEqual(validate_metadata(metadata(source=replace(
            metadata().source, device_id='', node='',
        ))), [])

    def test_unit_and_enum_contract(self):
        for state in UnitStatus:
            self.assertEqual(validate_metadata(metadata(unit='g', unit_status=state)), [])
        for state in (UnitStatus.INFERRED, UnitStatus.VERIFIED):
            self.assertTrue(validate_metadata(metadata(unit_status=state)))
        for name in ('data_level', 'unit_status'):
            self.assertTrue(validate_metadata(metadata(**{name: 255})))
        for state in DataLevel:
            self.assertEqual(validate_metadata(metadata(data_level=state)), [])

    def test_timestamp_semantics_and_unknown_domain(self):
        for semantics in TimeSemantics:
            stamp = Stamp() if semantics == TimeSemantics.UNKNOWN else Stamp(-1, 500000000)
            self.assertEqual(validate_time(ObservationTime(stamp, semantics)), [])
        self.assertEqual(validate_time(ObservationTime(Stamp(), TimeSemantics.ROS_PUBLISH)), [])
        self.assertTrue(validate_time(ObservationTime(Stamp(1, 0))))
        self.assertTrue(validate_time(ObservationTime(semantics=255)))
        for stamp in (Stamp(0, -1), Stamp(0, 1000000000), Stamp(2147483648, 0),
                      Stamp(-2147483649, 0)):
            self.assertTrue(validate_time(ObservationTime(stamp, TimeSemantics.ROS_PUBLISH)))

    def test_additional_times_do_not_require_synchronization(self):
        primary = ObservationTime(Stamp(100, 1), TimeSemantics.DEVICE_MEASUREMENT,
                                  'device-boot-a', 'device')
        extra = ObservationTime(Stamp(50, 2), TimeSemantics.ROS_PUBLISH,
                                'ros-run-b', 'source-publisher')
        candidate = metadata(observation_time=primary, additional_times=(extra,))
        self.assertEqual(validate_metadata(candidate), [])
        bad = replace(extra, stamp=Stamp(0, -1))
        errors = validate_metadata(replace(candidate, additional_times=(extra, bad)))
        self.assertTrue(any('additional_times[1]' in error for error in errors))
        self.assertEqual(candidate.observation_time, primary)

    def test_quality_has_native_scale_not_confidence(self):
        self.assertEqual(validate_quality(ObservationQuality()), [])
        for state in Validity:
            self.assertEqual(validate_quality(ObservationQuality(validity=state)), [])
        for number in (0.0, 4.0, -2.0):
            self.assertEqual(validate_quality(ObservationQuality(
                quality_available=True, quality_value=number, quality_scale='native ordinal',
            )), [])
        for changes in ({'quality_value': 0.0}, {'quality_scale': 'native'},
                        {'quality_available': True}, {'validity': 255},
                        {'quality_available': True, 'quality_value': math.inf,
                         'quality_scale': 'native'}):
            self.assertTrue(validate_quality(replace(ObservationQuality(), **changes)))


class PayloadContractTest(unittest.TestCase):
    def test_scalar_valid_missing_and_other_states(self):
        for state in Validity:
            candidate = metadata(quality=ObservationQuality(validity=state))
            self.assertEqual(validate_scalar(ScalarObservation(
                candidate, 0.0 if state == Validity.VALID else math.nan,
            )), [])
        valid = metadata(quality=ObservationQuality(validity=Validity.VALID))
        self.assertTrue(validate_scalar(ScalarObservation(valid, math.nan)))
        missing = metadata(quality=ObservationQuality(validity=Validity.MISSING))
        self.assertTrue(validate_scalar(ScalarObservation(missing, 0.0)))
        for number in (math.inf, -math.inf):
            self.assertTrue(validate_scalar(ScalarObservation(metadata(), number)))

    def test_vector_is_one_sample_unknown_frame_allowed(self):
        valid = metadata(quality=ObservationQuality(validity=Validity.VALID))
        self.assertEqual(validate_vector(VectorObservation(valid, Vector3(0., -1., 2.))), [])
        for axis in ('x', 'y', 'z'):
            self.assertTrue(validate_vector(VectorObservation(
                valid, replace(Vector3(0., 1., 2.), **{axis: math.nan}),
            )))
        missing = metadata(quality=ObservationQuality(validity=Validity.MISSING))
        self.assertEqual(validate_vector(VectorObservation(
            missing, Vector3(math.nan, math.nan, math.nan),
        )), [])
        self.assertTrue(validate_vector(VectorObservation(missing, Vector3(math.nan, 0., math.nan))))
        self.assertEqual(validate_vector(VectorObservation(
            metadata(), Vector3(1., math.nan, 3.),
        )), [])
        self.assertTrue(validate_vector(VectorObservation(metadata(), Vector3(1., math.inf, 3.))))

    def test_future_polar_hr_mapping_contract(self):
        source = SimpleNamespace(hr=67, header=SimpleNamespace(stamp=Stamp(123, 456)))
        candidate = ScalarObservation(metadata(
            observation_type='heart_rate', unit='bpm', unit_status=UnitStatus.VERIFIED,
            observation_time=ObservationTime(source.header.stamp, TimeSemantics.ROS_PUBLISH),
            source=ObservationSource(device_type='polar_h10', topic='/polar_hr',
                                     message_type='ros4hc_msgs/msg/HR', field_path='hr'),
            data_level=DataLevel.DEVICE_DERIVED,
        ), float(source.hr))
        self.assertEqual(validate_scalar(candidate), [])
        self.assertEqual(candidate.value, 67.0)
        self.assertEqual(candidate.metadata.observation_time.stamp, source.header.stamp)
        self.assertEqual(candidate.metadata.quality.validity, Validity.UNKNOWN)
        self.assertFalse(candidate.metadata.quality.quality_available)
        self.assertTrue(math.isnan(candidate.metadata.quality.quality_value))
        self.assertEqual(candidate.metadata.quality.quality_scale, '')

    def test_future_polar_acc_mapping_preserves_values_and_unknowns(self):
        source = SimpleNamespace(vector=Vector3(-0.125, 0.5, 1.0),
                                 header=SimpleNamespace(stamp=Stamp(321, 987)))
        candidate = VectorObservation(metadata(
            observation_type='acceleration', unit='g', unit_status=UnitStatus.INFERRED,
            observation_time=ObservationTime(source.header.stamp, TimeSemantics.ROS_PUBLISH),
            source=ObservationSource(device_type='polar_h10', topic='/polar_acc',
                                     message_type='geometry_msgs/msg/Vector3Stamped',
                                     field_path='vector'), data_level=DataLevel.RAW_SAMPLE,
        ), Vector3(source.vector.x, source.vector.y, source.vector.z))
        self.assertEqual(validate_vector(candidate), [])
        self.assertEqual(candidate.value, source.vector)
        self.assertEqual(candidate.frame_id, '')
        self.assertEqual(candidate.metadata.unit_status, UnitStatus.INFERRED)
        self.assertEqual(candidate.metadata.observation_time.stamp, source.header.stamp)
        self.assertEqual(candidate.metadata.observation_time.clock_domain, '')
        self.assertEqual(candidate.metadata.additional_times, ())
        self.assertEqual(candidate.metadata.quality, ObservationQuality())


class MessageDefinitionTest(unittest.TestCase):
    def test_interfaces_match_models_and_legacy_enum_numbers(self):
        directory = Path(__file__).resolve().parents[2] / 'ros4hc_observation_msgs/msg'
        contracts = (
            (ObservationTime, {'TIME_': TimeSemantics}),
            (ObservationQuality, {'VALIDITY_': Validity}),
            (ObservationSource, {}),
            (ObservationMetadata, {'UNIT_STATUS_': UnitStatus, 'DATA_': DataLevel}),
            (ScalarObservation, {}), (VectorObservation, {}),
        )
        for model, enums in contracts:
            declared, names = {}, []
            for line in (directory / (model.__name__ + '.msg')).read_text().splitlines():
                line = line.split('#', 1)[0].strip()
                if not line:
                    continue
                if '=' in line:
                    name, value = line.split()[1].split('=')
                    declared[name] = int(value)
                else:
                    names.append(line.split()[1])
            self.assertEqual(names, [item.name for item in fields(model)])
            self.assertEqual(declared, {
                prefix + item.name: int(item) for prefix, enum in enums.items() for item in enum
            })


if __name__ == '__main__':
    unittest.main()

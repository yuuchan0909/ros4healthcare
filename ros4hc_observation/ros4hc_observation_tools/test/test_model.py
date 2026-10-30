"""Run with unittest without ROS, or with pytest/colcon in a ROS workspace."""

from dataclasses import replace
import math
from pathlib import Path
import unittest

from ros4hc_observation_tools.model import (
    DEFAULT_VALUES, DataLevel, Observation, ObservationTime, SyntheticConfig,
    SyntheticSequence, TimeSemantics, UnitStatus, Validity, validate_observation,
)


class ObservationModelTest(unittest.TestCase):
    def make_observation(self):
        return SyntheticSequence(SyntheticConfig()).next_observation(
            1234567890123, '/health_observations',
        )

    def test_default_sequence_and_completion(self):
        sequence = SyntheticSequence(SyntheticConfig())
        actual = [sequence.next_observation(i, '/hr').value for i in range(len(DEFAULT_VALUES))]
        self.assertEqual(tuple(actual), DEFAULT_VALUES)
        self.assertIsNone(sequence.next_observation(99, '/hr'))
        self.assertIsNone(sequence.next_observation(100, '/hr'))

    def test_configuration_and_repeat(self):
        config = SyntheticConfig(
            values=(91.0, 93.0), publish_period_sec=0.25,
            subject_id='subject_b', session_id='session_b',
            source_device_id='generator_b', repeat=True,
        )
        sequence = SyntheticSequence(config)
        observations = [sequence.next_observation(i, '/renamed') for i in range(3)]
        self.assertEqual([item.value for item in observations], [91.0, 93.0, 91.0])
        self.assertEqual(observations[0].subject_id, 'subject_b')
        self.assertEqual(observations[0].session_id, 'session_b')
        self.assertEqual(observations[0].source_device_id, 'generator_b')
        self.assertEqual(observations[0].source_topic, '/renamed')
        self.assertEqual(config.publish_period_sec, 0.25)

    def test_synthetic_metadata_and_no_invented_quality(self):
        observation = self.make_observation()
        self.assertEqual(validate_observation(observation), [])
        self.assertEqual(observation.observation_type, 'heart_rate')
        self.assertEqual(observation.unit, 'bpm')
        self.assertEqual(observation.unit_status, UnitStatus.VERIFIED)
        self.assertEqual(observation.data_level, DataLevel.SYNTHETIC_SAMPLE)
        self.assertNotEqual(observation.data_level, DataLevel.RAW_SAMPLE)
        self.assertEqual(observation.validity, Validity.VALID)
        self.assertFalse(observation.quality_available)
        self.assertTrue(math.isnan(observation.quality_value))
        self.assertEqual(observation.quality_scale, '')
        self.assertEqual(observation.source_device_type, 'synthetic')
        self.assertEqual(observation.source_message_type,
                         'ros4hc_observation_msgs/msg/HealthObservation')
        self.assertEqual(observation.source_field, 'value')

    def test_timestamp_and_each_semantics_are_preserved(self):
        observation = self.make_observation()
        self.assertEqual(observation.observation_time, ObservationTime(1234, 567890123))
        self.assertEqual(observation.time_semantics, TimeSemantics.ROS_PUBLISH)
        for semantics in TimeSemantics:
            stamp = ObservationTime() if semantics == TimeSemantics.UNKNOWN else ObservationTime(2, 3)
            candidate = replace(observation, observation_time=stamp, time_semantics=semantics)
            self.assertEqual(validate_observation(candidate), [])
            self.assertEqual(candidate.time_semantics, semantics)
        for stamp in (ObservationTime(-1, 500000000), ObservationTime(2147483647, 999999999)):
            self.assertEqual(validate_observation(replace(observation, observation_time=stamp)), [])

    def test_zero_is_not_unknown_or_unavailable_quality(self):
        observation = replace(
            self.make_observation(), value=0.0, observation_time=ObservationTime(),
            quality_available=True, quality_value=0.0, quality_scale='native 0-4',
        )
        self.assertEqual(validate_observation(observation), [])
        self.assertEqual(observation.validity, Validity.VALID)
        self.assertEqual(observation.time_semantics, TimeSemantics.ROS_PUBLISH)
        self.assertTrue(observation.quality_available)
        self.assertTrue(validate_observation(replace(observation, quality_available=False)))

    def test_unknown_metadata_is_allowed(self):
        observation = Observation(
            subject_id='s', session_id='session', observation_type='heart_rate',
            value=math.nan, source_topic='/unknown_source',
            source_message_type='ros4hc_observation_msgs/msg/HealthObservation',
            source_field='value',
        )
        self.assertEqual(validate_observation(observation), [])
        self.assertEqual(observation.validity, Validity.UNKNOWN)
        self.assertEqual(observation.unit_status, UnitStatus.UNKNOWN)
        self.assertEqual(observation.time_semantics, TimeSemantics.UNKNOWN)
        self.assertEqual(observation.data_level, DataLevel.UNKNOWN)

    def test_missing_value_and_structural_errors(self):
        observation = self.make_observation()
        self.assertEqual(validate_observation(replace(
            observation, validity=Validity.MISSING, value=math.nan)), [])
        invalid_changes = (
            {'subject_id': ''}, {'session_id': ' '}, {'source_topic': ''},
            {'observation_type': ''}, {'source_device_type': ''},
            {'source_message_type': ''}, {'source_field': ''},
            {'unit': ''}, {'unit_status': 255}, {'time_semantics': 255},
            {'data_level': 255}, {'validity': 255}, {'value': math.nan},
            {'validity': Validity.MISSING},
            {'time_semantics': TimeSemantics.UNKNOWN},
            {'observation_time': ObservationTime(0, 1000000000)},
            {'observation_time': ObservationTime(0, -1)},
            {'observation_time': ObservationTime(2147483648, 0)},
            {'observation_time': ObservationTime(-2147483649, 0)},
            {'quality_value': 0.0}, {'quality_scale': '0-1'},
            {'quality_available': True},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                self.assertTrue(validate_observation(replace(observation, **changes)))
        self.assertEqual(validate_observation(replace(
            observation, quality_available=True, quality_value=3.0,
            quality_scale='native ordinal 0=bad,4=good')), [])

    def test_invalid_configuration_rejected(self):
        for changes in (
            {'values': ()}, {'values': (math.nan,)}, {'values': (math.inf,)},
            {'publish_period_sec': 0.0}, {'publish_period_sec': -1.0},
            {'publish_period_sec': math.inf}, {'subject_id': ''},
            {'session_id': ' '}, {'source_device_id': ''},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                SyntheticConfig(**changes)

    def test_message_constants_match_model(self):
        message_path = (Path(__file__).resolve().parents[2] /
                        'ros4hc_observation_msgs/msg/HealthObservation.msg')
        declared = {}
        fields = []
        for line in message_path.read_text().splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if '=' in line:
                name, value = line.split()[1].split('=')
                self.assertNotIn(name, declared)
                self.assertEqual(line.split()[0], 'uint8')
                self.assertTrue(0 <= int(value) <= 255)
                declared[name] = int(value)
            else:
                fields.append(line.split()[1])
        expected = {}
        for prefix, enum_type in (
            ('UNIT_STATUS_', UnitStatus), ('TIME_', TimeSemantics),
            ('DATA_', DataLevel), ('VALIDITY_', Validity),
        ):
            codes = [value for name, value in declared.items() if name.startswith(prefix)]
            self.assertEqual(len(codes), len(set(codes)), prefix)
            expected.update({prefix + item.name: int(item) for item in enum_type})
        self.assertEqual(declared, expected)
        self.assertEqual(fields, list(Observation.__dataclass_fields__))


if __name__ == '__main__':
    unittest.main()

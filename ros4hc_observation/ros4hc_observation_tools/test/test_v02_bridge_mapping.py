"""ROS-independent schema migration checks."""

from dataclasses import replace
import math
import unittest

from ros4hc_observation_tools.model import (
    DataLevel, Observation, ObservationTime, TimeSemantics, UnitStatus, Validity,
)
from ros4hc_observation_tools.model_v02 import validate_scalar
from ros4hc_observation_tools.v02_bridge_mapping import map_health_observation


def sample():
    return Observation(
        subject_id='participant_7', session_id='session_8', observation_type='heart_rate',
        value=72.0, unit='bpm', unit_status=UnitStatus.VERIFIED,
        observation_time=ObservationTime(123, 456789012),
        time_semantics=TimeSemantics.ROS_PUBLISH,
        source_device_type='polar_h10', source_device_id='polar_9',
        source_topic='/polar_hr', source_message_type='ros4hc_msgs/msg/HR',
        source_field='hr', data_level=DataLevel.DEVICE_DERIVED,
        validity=Validity.UNKNOWN, quality_available=False, quality_value=math.nan,
    )


class BridgeMappingTest(unittest.TestCase):
    def test_all_original_fields_and_unknown_metadata(self):
        original = sample()
        result = map_health_observation(original)
        metadata = result.metadata
        self.assertEqual(result.value, original.value)
        for name in ('subject_id', 'session_id', 'observation_type', 'unit',
                     'unit_status', 'data_level'):
            self.assertEqual(getattr(metadata, name), getattr(original, name))
        self.assertEqual(metadata.observation_time.stamp, original.observation_time)
        self.assertIsNot(metadata.observation_time.stamp, original.observation_time)
        self.assertEqual(metadata.observation_time.semantics, original.time_semantics)
        self.assertEqual(metadata.observation_time.clock_domain, '')
        self.assertEqual(metadata.observation_time.event_source, '')
        self.assertEqual(list(metadata.additional_times), [])
        for target, source in (
            ('device_type', 'source_device_type'), ('device_id', 'source_device_id'),
            ('topic', 'source_topic'), ('message_type', 'source_message_type'),
            ('field_path', 'source_field'),
        ):
            self.assertEqual(getattr(metadata.source, target), getattr(original, source))
        self.assertEqual(metadata.source.node, '')
        self.assertEqual(metadata.quality.validity, original.validity)
        self.assertFalse(metadata.quality.quality_available)
        self.assertTrue(math.isnan(metadata.quality.quality_value))
        self.assertEqual(metadata.quality.quality_scale, '')
        self.assertTrue(metadata.observation_id)
        self.assertNotEqual(metadata.observation_id,
                            map_health_observation(original).metadata.observation_id)
        self.assertEqual(validate_scalar(result), [])

    def test_nan_and_each_validity_are_preserved(self):
        for validity in Validity:
            with self.subTest(validity=validity):
                original = replace(sample(), value=math.nan, validity=validity)
                result = map_health_observation(original)
                self.assertTrue(math.isnan(result.value))
                self.assertEqual(result.metadata.quality.validity, validity)
                self.assertEqual(bool(validate_scalar(result)), validity == Validity.VALID)

    def test_quality_and_enum_fields_are_not_reinterpreted(self):
        for level in DataLevel:
            for status in UnitStatus:
                original = replace(sample(), data_level=level, unit_status=status,
                                   quality_available=True, quality_value=0.75,
                                   quality_scale='device score')
                result = map_health_observation(original)
                self.assertEqual(result.metadata.data_level, level)
                self.assertEqual(result.metadata.unit_status, status)
                self.assertTrue(result.metadata.quality.quality_available)
                self.assertEqual(result.metadata.quality.quality_value, 0.75)
                self.assertEqual(result.metadata.quality.quality_scale, 'device score')
                self.assertEqual(validate_scalar(result), [])

    def test_invalid_input_is_preserved_for_validator_rejection(self):
        original = replace(sample(), unit='', validity=Validity.VALID, value=math.nan)
        result = map_health_observation(original)
        self.assertEqual(result.metadata.unit, '')
        self.assertTrue(math.isnan(result.value))
        self.assertTrue(validate_scalar(result))

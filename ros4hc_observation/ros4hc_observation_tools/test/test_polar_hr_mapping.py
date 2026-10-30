"""Polar mapping tests using HR-shaped objects, without ROS or BLE dependencies."""

import math
from types import SimpleNamespace
import unittest

from ros4hc_observation_tools.model import (
    DataLevel, TimeSemantics, UnitStatus, Validity, validate_observation,
)
from ros4hc_observation_tools.polar_hr_mapping import PolarHRConfig, map_polar_hr


def hr_message(hr=105, sec=123, nanosec=456789012, quality=0):
    return SimpleNamespace(
        hr=hr, hr_quality=quality,
        header=SimpleNamespace(stamp=SimpleNamespace(sec=sec, nanosec=nanosec)),
    )


class PolarHRMappingTest(unittest.TestCase):
    def setUp(self):
        self.config = PolarHRConfig('subject_01', 'session_02', 'polar_03')

    def test_value_and_semantic_mapping(self):
        observation = map_polar_hr(hr_message(), self.config, '/remapped/polar_hr')
        self.assertEqual(validate_observation(observation), [])
        self.assertIsInstance(observation.value, float)
        self.assertEqual(observation.value, 105.0)
        self.assertEqual(observation.observation_type, 'heart_rate')
        self.assertEqual(observation.unit, 'bpm')
        self.assertEqual(observation.unit_status, UnitStatus.VERIFIED)
        self.assertEqual(observation.time_semantics, TimeSemantics.ROS_PUBLISH)
        self.assertEqual(observation.data_level, DataLevel.DEVICE_DERIVED)
        self.assertEqual(observation.validity, Validity.UNKNOWN)

    def test_stamp_is_copied_without_retiming_or_aliasing(self):
        for sec, nanosec in ((123, 456789012), (0, 0), (-1, 500000000)):
            with self.subTest(sec=sec, nanosec=nanosec):
                message = hr_message(sec=sec, nanosec=nanosec)
                observation = map_polar_hr(message, self.config, '/polar_hr')
                message.header.stamp.sec = 99
                self.assertEqual(observation.observation_time.sec, sec)
                self.assertEqual(observation.observation_time.nanosec, nanosec)
                self.assertEqual(observation.time_semantics, TimeSemantics.ROS_PUBLISH)

    def test_provenance_and_identifiers(self):
        observation = map_polar_hr(hr_message(), self.config, '/participant/polar_hr_remapped')
        self.assertEqual(observation.subject_id, 'subject_01')
        self.assertEqual(observation.session_id, 'session_02')
        self.assertEqual(observation.source_device_type, 'polar_h10')
        self.assertEqual(observation.source_device_id, 'polar_03')
        self.assertEqual(observation.source_topic, '/participant/polar_hr_remapped')
        self.assertEqual(observation.source_message_type, 'ros4hc_msgs/msg/HR')
        self.assertEqual(observation.source_field, 'hr')

    def test_quality_is_unavailable_regardless_of_hr_quality(self):
        for quality in (0, 1, 100, -1):
            with self.subTest(hr_quality=quality):
                observation = map_polar_hr(hr_message(quality=quality), self.config, '/polar_hr')
                self.assertFalse(observation.quality_available)
                self.assertTrue(math.isnan(observation.quality_value))
                self.assertEqual(observation.quality_scale, '')
                self.assertEqual(observation.validity, Validity.UNKNOWN)

    def test_quality_field_is_never_read(self):
        class HRWithoutAccessibleQuality:
            hr = 72
            header = hr_message().header

            @property
            def hr_quality(self):
                raise AssertionError('adapter must not inspect hr_quality')

        observation = map_polar_hr(HRWithoutAccessibleQuality(), self.config, '/polar_hr')
        self.assertFalse(observation.quality_available)
        self.assertTrue(math.isnan(observation.quality_value))

    def test_no_physiological_threshold_or_validity_inference(self):
        for value in (-2147483648, -1, 0, 72, 105, 2147483647):
            with self.subTest(hr=value):
                observation = map_polar_hr(hr_message(hr=value), self.config, '/polar_hr')
                self.assertEqual(observation.value, float(value))
                self.assertEqual(observation.validity, Validity.UNKNOWN)

    def test_device_id_may_be_unavailable(self):
        config = PolarHRConfig('subject', 'session')
        observation = map_polar_hr(hr_message(), config, '/polar_hr')
        self.assertEqual(observation.source_device_id, '')
        self.assertEqual(validate_observation(observation), [])

    def test_missing_subject_or_session_is_rejected(self):
        for subject, session in (('', 'session'), (' ', 'session'), ('subject', ''), ('subject', ' ')):
            with self.subTest(subject=subject, session=session), self.assertRaises(ValueError):
                PolarHRConfig(subject, session)

    def test_structural_errors_are_not_silently_replaced(self):
        with self.assertRaises(ValueError):
            map_polar_hr(hr_message(nanosec=1000000000), self.config, '/polar_hr')
        with self.assertRaises(ValueError):
            map_polar_hr(hr_message(), self.config, '')


if __name__ == '__main__':
    unittest.main()

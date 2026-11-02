"""Mapping contracts without ROS imports."""

import math
from types import SimpleNamespace as NS
import unittest

from ros4hc_observation_tools.model import DataLevel, TimeSemantics, UnitStatus, Validity
from ros4hc_observation_tools.model_v02 import validate_vector
from ros4hc_observation_tools.polar_acc_mapping import PolarAccConfig, map_polar_acc


def sample(frame_id=''):
    return NS(header=NS(stamp=NS(sec=123, nanosec=456789012), frame_id=frame_id),
              vector=NS(x=0.10, y=-0.05, z=0.98))


class PolarAccMappingTest(unittest.TestCase):
    def test_full_mapping(self):
        original = sample('provided_frame')
        result = map_polar_acc(original, PolarAccConfig('subject', 'session', 'device'), '/acc')
        m = result.metadata
        self.assertEqual((result.value.x, result.value.y, result.value.z), (0.10, -0.05, 0.98))
        self.assertEqual(result.frame_id, original.header.frame_id)
        self.assertEqual((m.subject_id, m.session_id), ('subject', 'session'))
        self.assertEqual(m.observation_type, 'acceleration')
        self.assertEqual((m.unit, m.unit_status), ('g', UnitStatus.INFERRED))
        self.assertEqual((m.observation_time.stamp.sec, m.observation_time.stamp.nanosec),
                         (123, 456789012))
        self.assertEqual(m.observation_time.semantics, TimeSemantics.ROS_PUBLISH)
        self.assertEqual(m.observation_time.clock_domain, '')
        self.assertEqual(m.observation_time.event_source, '')
        self.assertEqual(list(m.additional_times), [])
        self.assertEqual(m.source.device_type, 'polar_h10')
        self.assertEqual(m.source.device_id, 'device')
        self.assertEqual(m.source.topic, '/acc')
        self.assertEqual(m.source.message_type, 'geometry_msgs/msg/Vector3Stamped')
        self.assertEqual(m.source.field_path, 'vector')
        self.assertEqual(m.source.node, '')
        self.assertEqual(m.data_level, DataLevel.RAW_SAMPLE)
        self.assertEqual(m.quality.validity, Validity.UNKNOWN)
        self.assertFalse(m.quality.quality_available)
        self.assertTrue(math.isnan(m.quality.quality_value))
        self.assertEqual(m.quality.quality_scale, '')
        self.assertTrue(m.observation_id)
        self.assertEqual(validate_vector(result), [])
        original.header.stamp.sec = 999
        original.vector.x = 999.0
        self.assertEqual(result.metadata.observation_time.stamp.sec, 123)
        self.assertEqual(result.value.x, 0.10)

    def test_empty_frame_and_device_and_fresh_id(self):
        config = PolarAccConfig('subject', 'session')
        first = map_polar_acc(sample(), config, '/polar_acc')
        second = map_polar_acc(sample(), config, '/polar_acc')
        self.assertEqual(first.frame_id, '')
        self.assertEqual(first.metadata.source.device_id, '')
        self.assertNotEqual(first.metadata.observation_id, second.metadata.observation_id)
        self.assertEqual(validate_vector(first), [])

    def test_required_identity(self):
        for subject, session in (('', 's'), ('s', ''), (' ', 's'), ('s', ' ')):
            with self.subTest(subject=subject, session=session), self.assertRaises(ValueError):
                PolarAccConfig(subject, session)

    def test_nonfinite_contract_is_unchanged(self):
        for axis in ('x', 'y', 'z'):
            for number in (math.inf, -math.inf, math.nan):
                with self.subTest(axis=axis, number=number):
                    original = sample()
                    setattr(original.vector, axis, number)
                    result = map_polar_acc(original, PolarAccConfig('s', 't'), '/acc')
                    self.assertEqual(bool(validate_vector(result)), math.isinf(number))
                    if math.isnan(number):
                        self.assertTrue(math.isnan(getattr(result.value, axis)))
                    else:
                        self.assertEqual(getattr(result.value, axis), number)

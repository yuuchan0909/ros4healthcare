"""Generated v0.2 serialization contracts; skipped only when ROS is absent."""

import importlib.util
import math
import unittest

ROS_AVAILABLE = importlib.util.find_spec('rclpy') is not None
if ROS_AVAILABLE:
    from rclpy.serialization import deserialize_message, serialize_message
    from ros4hc_observation_msgs.msg import (
        ObservationMetadata, ObservationQuality, ObservationSource, ObservationTime,
        ScalarObservation, VectorObservation,
    )
    from ros4hc_observation_tools.model_v02 import validate_scalar, validate_vector


@unittest.skipUnless(ROS_AVAILABLE, 'ROS2 unavailable: v0.2 generated interfaces not tested')
class GeneratedV02ContractTest(unittest.TestCase):
    def make_metadata(self):
        result = ObservationMetadata()
        result.observation_id = 'session:42'
        result.subject_id = 'subject'
        result.session_id = 'session'
        result.observation_type = 'acceleration'
        result.unit = 'g'
        result.unit_status = ObservationMetadata.UNIT_STATUS_INFERRED
        result.data_level = ObservationMetadata.DATA_RAW_SAMPLE
        result.source = ObservationSource(
            device_type='polar_h10', topic='/polar_acc',
            message_type='geometry_msgs/msg/Vector3Stamped', field_path='vector',
        )
        result.quality.quality_value = math.nan
        result.observation_time.semantics = ObservationTime.TIME_ROS_PUBLISH
        result.observation_time.stamp.sec = 123
        result.observation_time.stamp.nanosec = 456
        result.additional_times = [ObservationTime()]
        return result

    def test_vector_round_trip_preserves_metadata_unknowns_and_values(self):
        message = VectorObservation()
        message.metadata = self.make_metadata()
        message.value.x, message.value.y, message.value.z = -0.125, 0.5, 1.0
        restored = deserialize_message(serialize_message(message), VectorObservation)
        self.assertEqual(validate_vector(restored), [])
        self.assertEqual(restored.metadata.observation_id, 'session:42')
        self.assertEqual(restored.metadata.unit_status, ObservationMetadata.UNIT_STATUS_INFERRED)
        self.assertEqual(restored.metadata.source.field_path, 'vector')
        self.assertEqual(restored.metadata.observation_time.stamp.nanosec, 456)
        self.assertEqual(restored.metadata.observation_time.clock_domain, '')
        self.assertEqual(restored.metadata.additional_times[0].semantics, ObservationTime.TIME_UNKNOWN)
        self.assertTrue(math.isnan(restored.metadata.quality.quality_value))
        self.assertEqual(restored.frame_id, '')
        self.assertEqual((restored.value.x, restored.value.y, restored.value.z), (-0.125, 0.5, 1.0))

    def test_scalar_valid_and_missing_round_trip(self):
        message = ScalarObservation()
        message.metadata = self.make_metadata()
        message.metadata.observation_type = 'heart_rate'
        message.metadata.unit = 'bpm'
        message.metadata.unit_status = ObservationMetadata.UNIT_STATUS_VERIFIED
        message.metadata.data_level = ObservationMetadata.DATA_DEVICE_DERIVED
        message.metadata.source.topic = '/polar_hr'
        message.metadata.source.message_type = 'ros4hc_msgs/msg/HR'
        message.metadata.source.field_path = 'hr'
        for state, number in ((ObservationQuality.VALIDITY_VALID, 67.0),
                              (ObservationQuality.VALIDITY_MISSING, math.nan)):
            message.metadata.quality.validity = state
            message.value = number
            restored = deserialize_message(serialize_message(message), ScalarObservation)
            self.assertEqual(validate_scalar(restored), [])
            self.assertEqual(restored.metadata.quality.validity, state)
            if math.isnan(number):
                self.assertTrue(math.isnan(restored.value))
            else:
                self.assertEqual(restored.value, number)


if __name__ == '__main__':
    unittest.main()

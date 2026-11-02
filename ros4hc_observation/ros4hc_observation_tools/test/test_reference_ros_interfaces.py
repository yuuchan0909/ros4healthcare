"""Reference generated interfaces; skip only if ROS itself is unavailable."""

import importlib.util
import math
import unittest

ROS_AVAILABLE = importlib.util.find_spec('rclpy') is not None
if ROS_AVAILABLE:
    from geometry_msgs.msg import Vector3Stamped
    from rclpy.serialization import deserialize_message, serialize_message
    from ros4hc_observation_msgs.msg import ReferenceDescriptor, ReferenceObservation
    from ros4hc_observation_tools.in_memory_reference import InMemoryReferenceResolver
    from ros4hc_observation_tools.model_v02 import validate_reference, validate_reference_descriptor


@unittest.skipUnless(ROS_AVAILABLE, 'ROS2 unavailable: reference generated interfaces not tested')
class GeneratedReferenceTest(unittest.TestCase):
    def test_descriptor_serialization(self):
        message = ReferenceDescriptor(scheme='future/scheme/v1', id='opaque',
                                      profile='future/profile/v1')
        restored = deserialize_message(serialize_message(message), ReferenceDescriptor)
        self.assertEqual(restored, message)
        self.assertEqual(validate_reference_descriptor(restored), [])

    def test_observation_round_trip_and_native_resolution(self):
        native = Vector3Stamped()
        native.header.stamp.sec = 42
        native.header.frame_id = 'native-frame'
        native.vector.x, native.vector.y, native.vector.z = 1.0, -2.0, 3.0
        resolver = InMemoryReferenceResolver()
        message_type = 'geometry_msgs/msg/Vector3Stamped'
        reference = resolver.register(native, message_type)
        message = ReferenceObservation()
        self.assertEqual(set(message.get_fields_and_field_types()), {'metadata', 'reference'})
        message.reference = ReferenceDescriptor(
            scheme=reference.scheme, id=reference.id, profile=reference.profile,
        )
        metadata = message.metadata
        metadata.observation_id = 'observation-1'
        metadata.subject_id = 'subject'
        metadata.session_id = 'session'
        metadata.observation_type = 'native_sample'
        metadata.source.device_type = 'unknown'
        metadata.source.topic = '/native'
        metadata.source.message_type = message_type
        metadata.source.field_path = '$'
        metadata.quality.quality_value = math.nan
        restored = deserialize_message(serialize_message(message), ReferenceObservation)
        self.assertEqual(validate_reference(restored), [])
        self.assertEqual(restored.reference, message.reference)
        self.assertEqual(restored.metadata.source.field_path, '$')
        self.assertEqual(restored.metadata.observation_id, metadata.observation_id)
        self.assertEqual(restored.metadata.source.message_type, message_type)
        self.assertTrue(math.isnan(restored.metadata.quality.quality_value))
        self.assertEqual(resolver.resolve(restored.reference, restored.metadata.source.message_type), native)


if __name__ == '__main__':
    unittest.main()

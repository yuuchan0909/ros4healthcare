"""Native snapshots, metadata contract and synthetic ROS graph."""
import math
import time
import unittest
from dataclasses import replace
from unittest.mock import Mock
import rclpy
from rclpy.parameter import Parameter
from rclpy.serialization import serialize_message
from sensomative_raw_msgs.msg import RawNotification
from ros4hc_observation_msgs.msg import ReferenceObservation
from ros4hc_observation_tools.in_memory_reference import InMemoryReferenceResolver, SCHEME
from ros4hc_observation_tools.model import DataLevel, TimeSemantics, UnitStatus, Validity
from ros4hc_observation_tools.model_v02 import validate_reference
from sensomative_raw_bridge.reference_mapping import ReferenceConfig, map_reference, epoch_time, PROFILE, MESSAGE_TYPE
from sensomative_raw_bridge.reference_adapter import SensomativeReferenceAdapter


def native():
    value = RawNotification(protocol_version=7, session_id='windows-capture',
        transport_frame_id=91, windows_sequence=52,
        windows_received_utc_ns=1_700_000_000_999_999_999,
        windows_monotonic_ns=987654321, wsl_received_utc_ns=1_700_000_001_000_000_001,
        wsl_monotonic_ns=123456789, characteristic_uuid='native-uuid',
        payload_length=16, payload=list(range(16)))
    value.ros_publish_time.sec, value.ros_publish_time.nanosec = 42, 123
    return value


class MappingTest(unittest.TestCase):
    def setUp(self):
        self.raw = native()
        self.resolver = InMemoryReferenceResolver()
        self.config = ReferenceConfig('subject', 'study-recording', 'device')
        self.observation = map_reference(self.raw, self.config, '/resolved/raw', self.resolver)

    def test_metadata(self):
        m = self.observation.metadata
        self.assertTrue(m.observation_id)
        self.assertEqual((m.subject_id, m.session_id), ('subject', 'study-recording'))
        self.assertNotEqual(m.session_id, self.raw.session_id)
        self.assertEqual(m.observation_type, 'sensomative_raw_payload')
        self.assertEqual((m.unit, m.unit_status, m.data_level), ('', UnitStatus.UNKNOWN, DataLevel.RAW_SAMPLE))
        self.assertEqual(m.quality.validity, Validity.UNKNOWN)
        self.assertFalse(m.quality.quality_available)
        self.assertTrue(math.isnan(m.quality.quality_value))
        self.assertEqual(m.quality.quality_scale, '')
        s = m.source
        self.assertEqual((s.device_type, s.device_id, s.topic, s.message_type, s.field_path, s.node),
                         ('sensomative', 'device', '/resolved/raw', MESSAGE_TYPE, '$', ''))
        self.assertEqual((self.observation.reference.scheme, self.observation.reference.profile), (SCHEME, PROFILE))
        self.assertEqual(validate_reference(self.observation), [])

    def test_times(self):
        m = self.observation.metadata
        t = m.observation_time
        self.assertEqual((t.stamp.sec, t.stamp.nanosec), (1700000000, 999999999))
        self.assertEqual((t.semantics, t.clock_domain, t.event_source),
                         (TimeSemantics.HOST_RECEIVE, 'unix_utc', 'windows_ble_callback'))
        self.assertEqual(len(m.additional_times), 2)
        a, b = m.additional_times
        self.assertEqual((a.stamp.sec, a.stamp.nanosec, a.semantics, a.event_source),
                         (1700000001, 1, TimeSemantics.HOST_RECEIVE, 'wsl_tcp_frame_receive'))
        self.assertEqual((b.stamp.sec, b.stamp.nanosec, b.semantics, b.clock_domain, b.event_source),
                         (42, 123, TimeSemantics.ROS_PUBLISH, 'raw_bridge_ros_clock', 'raw_bridge_publish'))
        # Huge unrelated monotonic values do not affect observation timestamps.
        self.raw.windows_monotonic_ns = 2**64-1
        self.raw.wsl_monotonic_ns = 2**64-2
        changed = map_reference(self.raw, self.config, '/raw', self.resolver)
        self.assertEqual(changed.metadata.observation_time, t)
        self.assertEqual(changed.metadata.additional_times, m.additional_times)

    def test_boundaries_and_optional_times(self):
        for ns, pair in [(999999999, (0, 999999999)), (1000000000, (1, 0)),
                         (1000000001, (1, 1)), (-1, (-1, 999999999))]:
            t = epoch_time(ns, TimeSemantics.HOST_RECEIVE, 'unix_utc', 'test')
            self.assertEqual((t.stamp.sec, t.stamp.nanosec), pair)
        self.raw.wsl_received_utc_ns = 0
        self.raw.ros_publish_time.sec = self.raw.ros_publish_time.nanosec = 0
        self.assertEqual(len(map_reference(self.raw, self.config, '/raw', self.resolver).metadata.additional_times), 0)
        with self.assertRaises(ValueError):
            epoch_time(2**63-1, TimeSemantics.HOST_RECEIVE, 'unix_utc', 'test')

    def test_whole_native_snapshot_and_isolation(self):
        o = self.observation
        restored = self.resolver.resolve(o.reference, o.metadata.source.message_type)
        self.assertEqual(restored, self.raw)  # All generated fields, including array and length.
        self.assertEqual(serialize_message(restored), serialize_message(self.raw))
        self.raw.payload[0] = 255
        self.raw.session_id = 'mutated'
        restored.payload[1] = 254
        restored.protocol_version = 99
        self.assertEqual(self.resolver.resolve(o.reference, MESSAGE_TYPE), native())

    def test_distinct_identities(self):
        other = map_reference(self.raw, self.config, '/raw', self.resolver)
        ids = [self.observation.metadata.observation_id, other.metadata.observation_id,
               self.observation.reference.id, other.reference.id, self.raw.session_id]
        self.assertEqual(len(set(ids)), len(ids))

    def test_reject_invalid_metadata(self):
        with self.assertRaises(ValueError):
            map_reference(self.raw, self.config, '', self.resolver)


class RosTest(unittest.TestCase):
    def test_publish_rejection_recovery_and_same_process_resolution(self):
        rclpy.init()
        adapter = peer = None
        try:
            adapter = SensomativeReferenceAdapter(parameter_overrides=[
                Parameter('subject_id', value='subject'), Parameter('session_id', value='recording')])
            peer = rclpy.create_node('synthetic_reference_test')
            received = []
            sub = peer.create_subscription(ReferenceObservation, '/reference_observations', received.append, 10)
            pub = peer.create_publisher(RawNotification, '/sensomative/raw', 10)
            deadline = time.monotonic() + 8
            while (pub.get_subscription_count() == 0 or adapter.publisher.get_subscription_count() == 0):
                self.assertLess(time.monotonic(), deadline, 'ROS discovery timeout')
                rclpy.spin_once(peer, timeout_sec=0.05)
                rclpy.spin_once(adapter, timeout_sec=0.05)
            # Verify callback rejects invalid metadata and remains usable.
            publisher = adapter.publisher
            adapter.publisher = Mock()
            adapter.config = replace(adapter.config, subject_id='valid')
            object.__setattr__(adapter.config, 'subject_id', '')
            adapter.on_raw(native())
            adapter.publisher.publish.assert_not_called()
            adapter.config = ReferenceConfig('subject', 'recording')
            adapter.publisher = publisher
            pub.publish(native())
            deadline = time.monotonic() + 8
            while not received:
                self.assertLess(time.monotonic(), deadline, 'ReferenceObservation timeout')
                rclpy.spin_once(adapter, timeout_sec=0.05)
                rclpy.spin_once(peer, timeout_sec=0.05)
            observation = received[0]
            self.assertEqual(validate_reference(observation), [])
            self.assertEqual(adapter.resolver.resolve(observation.reference,
                             observation.metadata.source.message_type), native())
        finally:
            if peer is not None:
                peer.destroy_node()
            if adapter is not None:
                adapter.destroy_node()
            rclpy.shutdown()


if __name__ == '__main__':
    unittest.main()

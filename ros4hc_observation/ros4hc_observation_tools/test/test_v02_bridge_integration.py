"""Actual DDS migration and rejection/recovery; requires ROS 2."""

import importlib.util
import math
import time
import unittest
from unittest.mock import patch

ROS_AVAILABLE = importlib.util.find_spec('rclpy') is not None
if ROS_AVAILABLE:
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from ros4hc_observation_msgs.msg import HealthObservation, ScalarObservation
    from ros4hc_observation_tools.health_observation_v02_bridge import HealthObservationV02Bridge
    from ros4hc_observation_tools.model import to_ros_message
    from ros4hc_observation_tools.model_v02 import validate_scalar
    from test_v02_bridge_mapping import sample


@unittest.skipUnless(ROS_AVAILABLE, 'ROS2/rclpy unavailable: bridge integration not tested')
class BridgeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.context = Context()
        rclpy.init(context=self.context)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.nodes = []

    def tearDown(self):
        for node in reversed(self.nodes):
            self.executor.remove_node(node)
            node.destroy_node()
        self.executor.shutdown()
        self.context.shutdown()

    def add_node(self, node):
        self.nodes.append(node)
        self.executor.add_node(node)
        return node

    def wait_for(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.05)
        self.assertTrue(predicate(), 'Timed out waiting for ROS discovery or delivery')

    def test_defaults(self):
        bridge = self.add_node(HealthObservationV02Bridge(context=self.context))
        self.assertEqual(bridge.subscription.topic_name, '/health_observations')
        self.assertEqual(bridge.publisher.topic_name, '/scalar_observations')
        for name in ('input_topic', 'output_topic'):
            self.assertTrue(bridge.describe_parameter(name).read_only)

    def test_dds_mapping_invalid_drop_and_recovery(self):
        bridge = self.add_node(HealthObservationV02Bridge(
            context=self.context, parameter_overrides=[
                Parameter('input_topic', value='/bridge_test/input'),
                Parameter('output_topic', value='/bridge_test/output'),
            ],
        ))
        peer = self.add_node(Node('bridge_test_peer', context=self.context))
        publisher = peer.create_publisher(HealthObservation, '/bridge_test/input', 10)
        received = []
        subscription = peer.create_subscription(
            ScalarObservation, '/bridge_test/output', received.append, 10,
        )
        self.wait_for(lambda: publisher.get_subscription_count() == 1 and
                      bridge.publisher.get_subscription_count() == 1)
        original = to_ros_message(sample(), HealthObservation)
        publisher.publish(original)
        self.wait_for(lambda: len(received) == 1)
        actual = received[0]
        self.assertEqual(validate_scalar(actual), [])
        self.assertEqual(actual.value, original.value)
        metadata = actual.metadata
        self.assertTrue(metadata.observation_id)
        for name in ('subject_id', 'session_id', 'observation_type', 'unit',
                     'unit_status', 'data_level'):
            self.assertEqual(getattr(metadata, name), getattr(original, name))
        self.assertEqual(metadata.observation_time.stamp, original.observation_time)
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
        self.assertEqual(metadata.quality.quality_available, original.quality_available)
        self.assertTrue(math.isnan(metadata.quality.quality_value))
        self.assertEqual(metadata.quality.quality_scale, original.quality_scale)
        # Wait for actual rejection, then send a valid message as an ordering barrier.
        original.subject_id = ''
        with patch.object(type(bridge.get_logger()), 'warning') as warning:
            publisher.publish(original)
            self.wait_for(lambda: warning.called)
        original.subject_id = sample().subject_id
        original.value = math.nan
        original.validity = HealthObservation.VALIDITY_MISSING
        publisher.publish(original)
        self.wait_for(lambda: len(received) >= 2)
        self.assertEqual(len(received), 2)
        self.assertTrue(math.isnan(received[1].value))
        self.assertEqual(validate_scalar(received[1]), [])
        self.assertNotEqual(metadata.observation_id, received[1].metadata.observation_id)
        peer.destroy_subscription(subscription)

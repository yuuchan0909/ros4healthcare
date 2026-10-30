"""Real generated HR -> adapter -> HealthObservation tests, requiring ROS 2."""

import importlib.util
import math
import time
import unittest

ROS_AVAILABLE = importlib.util.find_spec('rclpy') is not None
if ROS_AVAILABLE:
    # Missing generated interfaces are failures on ROS hosts, not reasons to skip.
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from rclpy.serialization import deserialize_message, serialize_message
    from ros4hc_msgs.msg import HR
    from ros4hc_observation_msgs.msg import HealthObservation
    from ros4hc_observation_tools.model import validate_observation
    from ros4hc_observation_tools.polar_hr_adapter import PolarHRAdapter
    from ros4hc_observation_tools.validator import ObservationValidator


@unittest.skipUnless(ROS_AVAILABLE, 'ROS2/rclpy unavailable: Polar adapter integration not tested')
class PolarHRIntegrationTest(unittest.TestCase):
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
        deadline = time.monotonic() + 10.0
        while not predicate() and time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=0.05)
        self.assertTrue(predicate(), 'Timed out waiting for ROS discovery or delivery')

    def test_default_topics_and_all_parameters_are_read_only(self):
        adapter = self.add_node(PolarHRAdapter(
            context=self.context, parameter_overrides=[
                Parameter('subject_id', value='participant'),
                Parameter('session_id', value='session'),
            ],
        ))
        self.assertEqual(adapter.subscription.topic_name, '/polar_hr')
        self.assertEqual(adapter.publisher.topic_name, '/health_observations')
        self.assertEqual(adapter.config.source_device_id, '')
        for name, replacement in (
            ('input_topic', '/different_input'), ('output_topic', '/different_output'),
            ('subject_id', 'other_subject'), ('session_id', 'other_session'),
            ('source_device_id', 'other_device'),
        ):
            with self.subTest(parameter=name):
                self.assertTrue(adapter.describe_parameter(name).read_only)
                previous = adapter.get_parameter(name).value
                result = adapter.set_parameters([Parameter(name, value=replacement)])[0]
                self.assertFalse(result.successful)
                self.assertEqual(adapter.get_parameter(name).value, previous)

    def test_subject_and_session_are_required_at_startup(self):
        for subject, session in (('', 's'), ('s', ''), (' ', 's'), ('s', ' ')):
            with self.subTest(subject=subject, session=session), self.assertRaises(ValueError):
                PolarHRAdapter(context=self.context, parameter_overrides=[
                    Parameter('subject_id', value=subject),
                    Parameter('session_id', value=session),
                ])

    def test_remapped_dds_path_and_serialized_mapping(self):
        adapter = self.add_node(PolarHRAdapter(
            context=self.context, namespace='/polar_adapter_test',
            cli_args=['--ros-args', '-r', '/polar_hr:=input_hr',
                      '-r', '/health_observations:=output_hr'],
            parameter_overrides=[
                Parameter('subject_id', value='participant_7'),
                Parameter('session_id', value='session_8'),
                Parameter('source_device_id', value='polar_9'),
            ],
        ))
        input_topic = '/polar_adapter_test/input_hr'
        output_topic = '/polar_adapter_test/output_hr'
        self.assertEqual(adapter.subscription.topic_name, input_topic)
        self.assertEqual(adapter.publisher.topic_name, output_topic)
        peer = self.add_node(Node('polar_adapter_test_peer', context=self.context))
        publisher = peer.create_publisher(HR, input_topic, 10)
        received = []
        subscription = peer.create_subscription(HealthObservation, output_topic, received.append, 10)
        validator = self.add_node(ObservationValidator(
            context=self.context, parameter_overrides=[Parameter('input_topic', value=output_topic)],
        ))
        self.wait_for(lambda: publisher.get_subscription_count() >= 1 and
                      adapter.publisher.get_subscription_count() >= 2)
        for index, (value, quality) in enumerate(((105, 0), (0, 100), (-1, -1))):
            with self.subTest(value=value, ignored_quality=quality):
                message = HR()
                message.hr = value
                message.hr_quality = quality
                message.header.stamp.sec = 123 + index
                message.header.stamp.nanosec = 456789012
                publisher.publish(message)
                self.wait_for(lambda: len(received) >= index + 1 and
                              validator.accepted_count >= index + 1)
                # Assert both actual topic receipt and explicit serialization round-trip.
                actual = received[index]
                decoded = deserialize_message(serialize_message(actual), HealthObservation)
                for observation in (actual, decoded):
                    self.assertEqual(validate_observation(observation), [])
                    self.assertEqual(observation.value, float(value))
                    self.assertEqual(observation.observation_type, 'heart_rate')
                    self.assertEqual(observation.unit, 'bpm')
                    self.assertEqual(observation.unit_status, HealthObservation.UNIT_STATUS_VERIFIED)
                    self.assertEqual(observation.observation_time.sec, 123 + index)
                    self.assertEqual(observation.observation_time.nanosec, 456789012)
                    self.assertEqual(observation.time_semantics, HealthObservation.TIME_ROS_PUBLISH)
                    self.assertEqual(observation.data_level, HealthObservation.DATA_DEVICE_DERIVED)
                    self.assertEqual(observation.validity, HealthObservation.VALIDITY_UNKNOWN)
                    self.assertFalse(observation.quality_available)
                    self.assertTrue(math.isnan(observation.quality_value))
                    self.assertEqual(observation.quality_scale, '')
                    self.assertEqual(observation.subject_id, 'participant_7')
                    self.assertEqual(observation.session_id, 'session_8')
                    self.assertEqual(observation.source_device_type, 'polar_h10')
                    self.assertEqual(observation.source_device_id, 'polar_9')
                    self.assertEqual(observation.source_topic, input_topic)
                    self.assertEqual(observation.source_message_type, 'ros4hc_msgs/msg/HR')
                    self.assertEqual(observation.source_field, 'hr')
        self.assertEqual(validator.rejected_count, 0)
        peer.destroy_subscription(subscription)


if __name__ == '__main__':
    unittest.main()

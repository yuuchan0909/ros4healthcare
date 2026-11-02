"""DDS and short 200 Hz synthetic smoke tests; no performance benchmark."""

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
    from rclpy.serialization import deserialize_message, serialize_message
    from geometry_msgs.msg import Vector3Stamped
    from ros4hc_observation_msgs.msg import VectorObservation
    from ros4hc_observation_tools.model_v02 import validate_vector
    from ros4hc_observation_tools.polar_acc_adapter import PolarAccAdapter
    from ros4hc_observation_tools.synthetic_polar_acc import SyntheticPolarAccPublisher


@unittest.skipUnless(ROS_AVAILABLE, 'ROS2/rclpy unavailable: acceleration integration not tested')
class PolarAccIntegrationTest(unittest.TestCase):
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
            self.executor.spin_once(timeout_sec=0.01)
        self.assertTrue(predicate(), 'Timed out waiting for ROS discovery or delivery')

    def adapter(self, **kwargs):
        return self.add_node(PolarAccAdapter(context=self.context, parameter_overrides=[
            Parameter('subject_id', value='synthetic_subject'),
            Parameter('session_id', value='synthetic_session'),
        ], **kwargs))

    def assert_metadata(self, actual, topic, device=''):
        self.assertEqual(validate_vector(actual), [])
        m = actual.metadata
        self.assertTrue(m.observation_id)
        self.assertEqual(m.subject_id, 'synthetic_subject')
        self.assertEqual(m.session_id, 'synthetic_session')
        self.assertEqual(m.observation_type, 'acceleration')
        self.assertEqual(m.unit, 'g')
        self.assertEqual(m.unit_status, 2)  # INFERRED
        self.assertEqual(m.data_level, 1)  # RAW_SAMPLE
        self.assertEqual(m.observation_time.semantics, 3)  # ROS_PUBLISH
        self.assertEqual(m.observation_time.clock_domain, '')
        self.assertEqual(m.observation_time.event_source, '')
        self.assertEqual(list(m.additional_times), [])
        self.assertEqual(m.source.device_type, 'polar_h10')
        self.assertEqual(m.source.device_id, device)
        self.assertEqual(m.source.topic, topic)
        self.assertEqual(m.source.message_type, 'geometry_msgs/msg/Vector3Stamped')
        self.assertEqual(m.source.field_path, 'vector')
        self.assertEqual(m.source.node, '')
        self.assertEqual(m.quality.validity, 0)
        self.assertFalse(m.quality.quality_available)
        self.assertTrue(math.isnan(m.quality.quality_value))
        self.assertEqual(m.quality.quality_scale, '')

    def test_defaults_and_required_identity(self):
        adapter = self.adapter()
        self.assertEqual(adapter.subscription.topic_name, '/polar_acc')
        self.assertEqual(adapter.publisher.topic_name, '/vector_observations')
        for name in ('input_topic', 'output_topic', 'subject_id', 'session_id', 'source_device_id'):
            self.assertTrue(adapter.describe_parameter(name).read_only)
        for subject, session in (('', 's'), ('s', ''), (' ', 's'), ('s', ' ')):
            with self.subTest(subject=subject, session=session), self.assertRaises(ValueError):
                PolarAccAdapter(context=self.context, parameter_overrides=[
                    Parameter('subject_id', value=subject), Parameter('session_id', value=session),
                ])

    def test_remapped_dds_metadata_nonfinite_rejection_and_recovery(self):
        adapter = self.add_node(PolarAccAdapter(
            context=self.context, namespace='/acc_test',
            parameter_overrides=[
                Parameter('subject_id', value='synthetic_subject'),
                Parameter('session_id', value='synthetic_session'),
                Parameter('source_device_id', value='fixture'),
                Parameter('input_topic', value='configured_input'),
                Parameter('output_topic', value='configured_output'),
            ],
            cli_args=['--ros-args', '-r', 'configured_input:=input',
                      '-r', 'configured_output:=output'],
        ))
        self.assertEqual(adapter.subscription.topic_name, '/acc_test/input')
        self.assertEqual(adapter.publisher.topic_name, '/acc_test/output')
        peer = self.add_node(Node('acc_test_peer', context=self.context))
        publisher = peer.create_publisher(Vector3Stamped, '/acc_test/input', 10)
        received = []
        subscription = peer.create_subscription(
            VectorObservation, '/acc_test/output', received.append, 10,
        )
        self.wait_for(lambda: publisher.get_subscription_count() == 1 and
                      adapter.publisher.get_subscription_count() == 1)
        message = Vector3Stamped()
        message.header.stamp.sec = 123
        message.header.stamp.nanosec = 456789012
        message.header.frame_id = 'provided_frame'
        message.vector.x, message.vector.y, message.vector.z = 0.1, -0.05, 0.98
        publisher.publish(message)
        self.wait_for(lambda: len(received) == 1)
        for actual in (received[0], deserialize_message(serialize_message(received[0]), VectorObservation)):
            self.assert_metadata(actual, '/acc_test/input', 'fixture')
            self.assertEqual((actual.value.x, actual.value.y, actual.value.z), (0.1, -0.05, 0.98))
            self.assertEqual(actual.metadata.observation_time.stamp, message.header.stamp)
            self.assertEqual(actual.frame_id, 'provided_frame')
        message.vector.x = math.inf
        with patch.object(type(adapter.get_logger()), 'warning') as warning:
            publisher.publish(message)
            self.wait_for(lambda: warning.called)
        self.assertEqual(len(received), 1)
        # The accepted message after rejection also checks the existing NaN contract.
        message.vector.x = math.nan
        message.header.frame_id = ''
        publisher.publish(message)
        self.wait_for(lambda: len(received) >= 2)
        self.assertEqual(len(received), 2)
        self.assertTrue(math.isnan(received[1].value.x))
        self.assertEqual(received[1].frame_id, '')
        self.assert_metadata(received[1], '/acc_test/input', 'fixture')
        peer.destroy_subscription(subscription)

    def test_synthetic_200hz_e2e(self):
        adapter = self.adapter(cli_args=['--ros-args', '-r', '/polar_acc:=/acc_e2e/input',
                                        '-r', '/vector_observations:=/acc_e2e/output'])
        synthetic = self.add_node(SyntheticPolarAccPublisher(
            context=self.context, parameter_overrides=[
                Parameter('output_topic', value='/acc_e2e/input'),
            ],
        ))
        self.assertEqual(synthetic.get_parameter('publish_rate_hz').value, 200.0)
        self.assertEqual(synthetic.timer.timer_period_ns, 5000000)
        peer = self.add_node(Node('acc_e2e_peer', context=self.context))
        inputs, outputs = [], []
        sub_in = peer.create_subscription(Vector3Stamped, '/acc_e2e/input', inputs.append, 10)
        sub_out = peer.create_subscription(VectorObservation, '/acc_e2e/output', outputs.append, 10)
        self.wait_for(lambda: synthetic.publisher.get_subscription_count() == 2 and
                      adapter.publisher.get_subscription_count() == 1)
        inputs.clear()
        outputs.clear()
        self.wait_for(lambda: len(outputs) >= 10 and len(inputs) >= 10)
        synthetic.timer.cancel()
        stamps = {(msg.header.stamp.sec, msg.header.stamp.nanosec): msg for msg in inputs}
        matches = [msg for msg in outputs if
                   (msg.metadata.observation_time.stamp.sec,
                    msg.metadata.observation_time.stamp.nanosec) in stamps]
        self.assertTrue(matches)
        for actual in matches:
            self.assert_metadata(actual, '/acc_e2e/input')
            self.assertEqual((actual.value.x, actual.value.y, actual.value.z), (0.1, -0.05, 0.98))
            self.assertEqual(actual.frame_id, '')
        self.assertEqual(len({msg.metadata.observation_id for msg in outputs}), len(outputs))
        peer.destroy_subscription(sub_in)
        peer.destroy_subscription(sub_out)

    def test_synthetic_parameters_and_invalid_configuration(self):
        synthetic = self.add_node(SyntheticPolarAccPublisher(
            context=self.context, parameter_overrides=[
                Parameter('x', value=0.2), Parameter('y', value=-0.3), Parameter('z', value=1.1),
                Parameter('publish_rate_hz', value=50.0),
                Parameter('output_topic', value='/acc_config/output'),
            ],
        ))
        self.assertEqual(synthetic.xyz, (0.2, -0.3, 1.1))
        self.assertEqual(synthetic.timer.timer_period_ns, 20000000)
        self.assertEqual(synthetic.publisher.topic_name, '/acc_config/output')
        for name in ('x', 'y', 'z', 'publish_rate_hz', 'output_topic'):
            self.assertTrue(synthetic.describe_parameter(name).read_only)
        for name, number in (('publish_rate_hz', 0.0), ('publish_rate_hz', -1.0),
                             ('publish_rate_hz', math.inf), ('x', math.nan)):
            with self.subTest(name=name, number=number), self.assertRaises(ValueError):
                SyntheticPolarAccPublisher(context=self.context, parameter_overrides=[
                    Parameter(name, value=number),
                ])

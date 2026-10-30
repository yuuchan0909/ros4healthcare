"""Generated interface, serialization and DDS tests; require sourced ROS builds."""

from dataclasses import replace
import importlib.util
import math
import time
import unittest

ROS_AVAILABLE = importlib.util.find_spec('rclpy') is not None
if ROS_AVAILABLE:
    # If ROS is installed but the new message is not built/sourced, fail rather than skip.
    import rclpy
    from rclpy.context import Context
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from rclpy.serialization import deserialize_message, serialize_message
    from ros4hc_observation_msgs.msg import HealthObservation
    from ros4hc_observation_tools.model import (
        ObservationTime, SyntheticConfig, SyntheticSequence, TimeSemantics,
        UnitStatus, Validity, to_ros_message, validate_observation,
    )
    from ros4hc_observation_tools.synthetic_hr import SyntheticHRPublisher
    from ros4hc_observation_tools.validator import ObservationValidator


@unittest.skipUnless(ROS_AVAILABLE, 'ROS2/rclpy unavailable: generated message and DDS not tested')
class GeneratedMessageTest(unittest.TestCase):
    def test_generated_interface_and_serialization(self):
        observation = SyntheticSequence(SyntheticConfig()).next_observation(123000000456, '/hr')
        message = to_ros_message(observation, HealthObservation)
        decoded = deserialize_message(serialize_message(message), HealthObservation)
        self.assertEqual(validate_observation(decoded), [])
        self.assertEqual(decoded.value, 72.0)
        self.assertEqual(decoded.unit_status, HealthObservation.UNIT_STATUS_VERIFIED)
        self.assertEqual(decoded.data_level, HealthObservation.DATA_SYNTHETIC_SAMPLE)
        self.assertEqual(decoded.time_semantics, HealthObservation.TIME_ROS_PUBLISH)
        self.assertEqual((decoded.observation_time.sec, decoded.observation_time.nanosec),
                         (123, 456))
        self.assertTrue(math.isnan(decoded.quality_value))
        self.assertFalse(decoded.quality_available)
        for field in observation.__dataclass_fields__:
            if field not in ('quality_value', 'observation_time'):
                self.assertEqual(getattr(decoded, field), getattr(observation, field))
        # The timestamp meaning is independent of the synthetic data level.
        for semantics in TimeSemantics:
            with self.subTest(semantics=semantics):
                candidate = replace(
                    observation, time_semantics=semantics,
                    observation_time=(ObservationTime() if semantics == TimeSemantics.UNKNOWN
                                      else ObservationTime(12, 345)),
                )
                restored = deserialize_message(
                    serialize_message(to_ros_message(candidate, HealthObservation)),
                    HealthObservation,
                )
                self.assertEqual(restored.time_semantics, int(semantics))
                self.assertEqual(restored.data_level, HealthObservation.DATA_SYNTHETIC_SAMPLE)
                self.assertEqual(restored.observation_time.sec, candidate.observation_time.sec)
                self.assertEqual(restored.observation_time.nanosec,
                                 candidate.observation_time.nanosec)
                self.assertEqual(validate_observation(restored), [])

    def test_unknowns_survive_wire_serialization(self):
        observation = SyntheticSequence(SyntheticConfig()).next_observation(1, '/hr')
        observation = replace(
            observation, value=math.nan, unit='', unit_status=UnitStatus.UNKNOWN,
            time_semantics=TimeSemantics.UNKNOWN, observation_time=ObservationTime(),
            validity=Validity.UNKNOWN,
        )
        decoded = deserialize_message(
            serialize_message(to_ros_message(observation, HealthObservation)), HealthObservation,
        )
        self.assertEqual(validate_observation(decoded), [])
        self.assertEqual(decoded.time_semantics, HealthObservation.TIME_UNKNOWN)
        self.assertEqual(decoded.validity, HealthObservation.VALIDITY_UNKNOWN)
        self.assertEqual(decoded.unit_status, HealthObservation.UNIT_STATUS_UNKNOWN)
        self.assertTrue(math.isnan(decoded.value))
        self.assertTrue(math.isnan(decoded.quality_value))


@unittest.skipUnless(ROS_AVAILABLE, 'ROS2/rclpy unavailable: topic path not tested')
class TopicPathTest(unittest.TestCase):
    def test_parameters_resolved_source_topic_delivery_and_validator(self):
        context = Context()
        rclpy.init(context=context)
        executor = SingleThreadedExecutor(context=context)
        nodes = []
        try:
            publisher = SyntheticHRPublisher(
                context=context, namespace='/observation_test',
                cli_args=['--ros-args', '-r', 'health_observations:=renamed_hr'],
                parameter_overrides=[
                    Parameter('values', value=[91.0, 93.0]),
                    Parameter('publish_period_sec', value=0.03),
                    Parameter('subject_id', value='test_subject'),
                    Parameter('session_id', value='test_session'),
                    Parameter('source_device_id', value='test_generator'),
                    Parameter('repeat', value=True),
                ],
            )
            nodes.append(publisher)
            topic = '/observation_test/renamed_hr'
            validator = ObservationValidator(
                context=context, parameter_overrides=[Parameter('input_topic', value=topic)],
            )
            nodes.append(validator)
            observer = Node('test_observer', context=context)
            nodes.append(observer)
            received = []
            subscription = observer.create_subscription(HealthObservation, topic, received.append, 10)
            for node in nodes:
                executor.add_node(node)
            deadline = time.monotonic() + 10.0
            while (len(received) < 4 or validator.accepted_count < 4) and time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.05)
            self.assertGreaterEqual(len(received), 4)
            self.assertGreaterEqual(validator.accepted_count, 4)
            self.assertEqual(validator.rejected_count, 0)
            self.assertEqual(publisher.timer.timer_period_ns, 30000000)
            self.assertEqual(publisher.publisher.topic_name, topic)
            for message in received:
                self.assertIn(message.value, [91.0, 93.0])
                self.assertEqual(message.subject_id, 'test_subject')
                self.assertEqual(message.session_id, 'test_session')
                self.assertEqual(message.source_device_id, 'test_generator')
                self.assertEqual(message.source_topic, topic)
                self.assertEqual(message.time_semantics, HealthObservation.TIME_ROS_PUBLISH)
                self.assertEqual(message.data_level, HealthObservation.DATA_SYNTHETIC_SAMPLE)
                self.assertFalse(message.quality_available)
                self.assertTrue(math.isnan(message.quality_value))
                self.assertEqual(message.quality_scale, '')
            result = publisher.set_parameters([Parameter('subject_id', value='changed')])[0]
            self.assertFalse(result.successful)  # Startup-only parameters are explicit.
            publisher.timer.cancel()
            bad_message = received[-1]
            bad_message.subject_id = ''
            publisher.publisher.publish(bad_message)
            deadline = time.monotonic() + 3.0
            while validator.rejected_count == 0 and time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.05)
            self.assertEqual(validator.rejected_count, 1)
            observer.destroy_subscription(subscription)
        finally:
            for node in reversed(nodes):
                executor.remove_node(node)
                node.destroy_node()
            executor.shutdown()
            context.shutdown()


if __name__ == '__main__':
    unittest.main()

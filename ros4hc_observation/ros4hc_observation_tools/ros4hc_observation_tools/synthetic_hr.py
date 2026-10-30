"""Publish an explicitly synthetic HR sequence as scalar observations."""

import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from ros4hc_observation_msgs.msg import HealthObservation

from .model import DEFAULT_VALUES, SyntheticConfig, SyntheticSequence, to_ros_message


class SyntheticHRPublisher(Node):
    def __init__(self, **kwargs):
        super().__init__('synthetic_hr', **kwargs)
        defaults = {
            'values': list(DEFAULT_VALUES),
            'publish_period_sec': 1.0,
            'subject_id': 'synthetic_subject',
            'session_id': 'synthetic_session',
            'source_device_id': 'synthetic_hr_001',
            'repeat': False,
            'output_topic': 'health_observations',
        }
        for name, default in defaults.items():
            self.declare_parameter(
                name, default,
                ParameterDescriptor(read_only=True, description='Set at node startup.'),
            )
        config = SyntheticConfig(**{
            name: (tuple(self.get_parameter(name).value) if name == 'values'
                   else self.get_parameter(name).value)
            for name in defaults if name != 'output_topic'
        })
        self.sequence = SyntheticSequence(config)
        self.publisher = self.create_publisher(
            HealthObservation, self.get_parameter('output_topic').value, 10,
        )
        self.timer = self.create_timer(config.publish_period_sec, self.publish_next)
        self.get_logger().info(
            f'Synthetic HR: {len(config.values)} samples, period={config.publish_period_sec}s, '
            f'topic={self.publisher.topic_name}; quality unavailable',
        )

    def publish_next(self):
        # Synthetic describes the data; the timestamp describes the ROS publish event.
        observation = self.sequence.next_observation(
            self.get_clock().now().nanoseconds, self.publisher.topic_name,
        )
        if observation is None:
            self.timer.cancel()
            self.get_logger().info('Synthetic sequence complete; publisher timer stopped.')
            return
        self.publisher.publish(to_ros_message(observation, HealthObservation))


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = SyntheticHRPublisher()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

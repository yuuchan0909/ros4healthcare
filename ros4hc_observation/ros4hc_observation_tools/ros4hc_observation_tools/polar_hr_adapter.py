"""Adapt Polar ros4hc_msgs/HR into scalar HealthObservation messages."""

import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from ros4hc_msgs.msg import HR
from ros4hc_observation_msgs.msg import HealthObservation

from .model import to_ros_message
from .polar_hr_mapping import PolarHRConfig, map_polar_hr


class PolarHRAdapter(Node):
    def __init__(self, **kwargs):
        super().__init__('polar_hr_adapter', **kwargs)
        defaults = {
            'input_topic': '/polar_hr',
            'output_topic': '/health_observations',
            'subject_id': '',
            'session_id': '',
            'source_device_id': '',
        }
        for name, default in defaults.items():
            self.declare_parameter(
                name, default,
                ParameterDescriptor(read_only=True, description='Set at node startup.'),
            )
        try:
            self.config = PolarHRConfig(**{
                name: self.get_parameter(name).value
                for name in ('subject_id', 'session_id', 'source_device_id')
            })
        except ValueError:
            self.destroy_node()
            raise
        self.publisher = self.create_publisher(
            HealthObservation, self.get_parameter('output_topic').value, 10,
        )
        self.subscription = self.create_subscription(
            HR, self.get_parameter('input_topic').value, self.on_hr, 10,
        )
        self.get_logger().info(
            f'Polar HR: {self.subscription.topic_name} -> {self.publisher.topic_name}; '
            'preserving source ROS publish stamp; validity unknown; quality unavailable',
        )

    def on_hr(self, message):
        try:
            observation = map_polar_hr(message, self.config, self.subscription.topic_name)
        except ValueError as error:
            self.get_logger().error(f'Cannot map structurally invalid Polar HR: {error}')
            return
        self.publisher.publish(to_ros_message(observation, HealthObservation))


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = PolarHRAdapter()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

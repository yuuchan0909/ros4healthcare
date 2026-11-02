"""Adapt Polar Vector3Stamped samples to v0.2 VectorObservation."""

import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from geometry_msgs.msg import Vector3Stamped
from ros4hc_observation_msgs.msg import VectorObservation

from .model_v02 import validate_vector
from .polar_acc_mapping import PolarAccConfig, map_polar_acc, to_vector_message


class PolarAccAdapter(Node):
    def __init__(self, **kwargs):
        super().__init__('polar_acc_adapter', **kwargs)
        defaults = {
            'input_topic': '/polar_acc', 'output_topic': '/vector_observations',
            'subject_id': '', 'session_id': '', 'source_device_id': '',
        }
        for name, default in defaults.items():
            self.declare_parameter(
                name, default,
                ParameterDescriptor(read_only=True, description='Set at node startup.'),
            )
        try:
            self.config = PolarAccConfig(**{
                name: self.get_parameter(name).value
                for name in ('subject_id', 'session_id', 'source_device_id')
            })
        except ValueError:
            self.destroy_node()
            raise
        self.publisher = self.create_publisher(
            VectorObservation, self.get_parameter('output_topic').value, 10,
        )
        self.subscription = self.create_subscription(
            Vector3Stamped, self.get_parameter('input_topic').value, self.on_acc, 10,
        )

    def on_acc(self, message):
        observation = to_vector_message(
            map_polar_acc(message, self.config, self.subscription.topic_name), VectorObservation,
        )
        errors = validate_vector(observation)
        if errors:
            self.get_logger().warning('Rejecting invalid acceleration: ' + '; '.join(errors))
            return
        self.publisher.publish(observation)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = PolarAccAdapter()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

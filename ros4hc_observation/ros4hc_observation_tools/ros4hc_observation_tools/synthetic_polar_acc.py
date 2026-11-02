"""Publish constant acceleration fixtures; does not simulate Polar BLE behavior."""

import math

import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from geometry_msgs.msg import Vector3Stamped


class SyntheticPolarAccPublisher(Node):
    def __init__(self, **kwargs):
        super().__init__('synthetic_polar_acc', **kwargs)
        defaults = {
            'x': 0.10, 'y': -0.05, 'z': 0.98, 'publish_rate_hz': 200.0,
            'output_topic': '/polar_acc',
        }
        for name, default in defaults.items():
            self.declare_parameter(
                name, default,
                ParameterDescriptor(read_only=True, description='Set at node startup.'),
            )
        rate = self.get_parameter('publish_rate_hz').value
        self.xyz = tuple(self.get_parameter(name).value for name in ('x', 'y', 'z'))
        if not math.isfinite(rate) or rate <= 0 or not all(map(math.isfinite, self.xyz)):
            self.destroy_node()
            raise ValueError('publish_rate_hz must be finite and positive; xyz must be finite')
        self.publisher = self.create_publisher(
            Vector3Stamped, self.get_parameter('output_topic').value, 10,
        )
        self.timer = self.create_timer(1.0 / rate, self.publish_next)
        self.get_logger().info('Synthetic acceleration fixture; no Polar BLE/timing simulation')

    def publish_next(self):
        message = Vector3Stamped()
        message.vector.x, message.vector.y, message.vector.z = self.xyz
        message.header.frame_id = ''
        message.header.stamp = self.get_clock().now().to_msg()
        self.publisher.publish(message)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = SyntheticPolarAccPublisher()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

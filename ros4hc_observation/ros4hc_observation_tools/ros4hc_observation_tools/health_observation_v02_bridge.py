"""Migrate v0.1 HealthObservation to v0.2 ScalarObservation."""

import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from ros4hc_observation_msgs.msg import HealthObservation, ScalarObservation

from .model_v02 import validate_scalar
from .v02_bridge_mapping import map_health_observation, to_scalar_message


class HealthObservationV02Bridge(Node):
    def __init__(self, **kwargs):
        super().__init__('health_observation_v02_bridge', **kwargs)
        for name, default in (
            ('input_topic', '/health_observations'),
            ('output_topic', '/scalar_observations'),
        ):
            self.declare_parameter(
                name, default,
                ParameterDescriptor(read_only=True, description='Set at node startup.'),
            )
        self.publisher = self.create_publisher(
            ScalarObservation, self.get_parameter('output_topic').value, 10,
        )
        self.subscription = self.create_subscription(
            HealthObservation, self.get_parameter('input_topic').value,
            self.on_observation, 10,
        )

    def on_observation(self, message):
        observation = to_scalar_message(map_health_observation(message), ScalarObservation)
        errors = validate_scalar(observation)
        if errors:
            self.get_logger().warning('Rejecting invalid v0.2 observation: ' + '; '.join(errors))
            return
        self.publisher.publish(observation)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = HealthObservationV02Bridge()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

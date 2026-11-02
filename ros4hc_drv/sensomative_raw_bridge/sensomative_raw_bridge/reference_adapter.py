"""Process-local native RawNotification reference prototype."""
import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from sensomative_raw_msgs.msg import RawNotification
from ros4hc_observation_msgs.msg import ReferenceObservation
from ros4hc_observation_tools.in_memory_reference import InMemoryReferenceResolver
from .reference_mapping import ReferenceConfig, map_reference


class SensomativeReferenceAdapter(Node):
    def __init__(self, **kwargs):
        super().__init__('sensomative_reference_adapter', **kwargs)
        defaults = dict(input_topic='/sensomative/raw', output_topic='/reference_observations',
                        subject_id='', session_id='', source_device_id='',
                        observation_type='sensomative_raw_payload')
        for name, value in defaults.items():
            self.declare_parameter(name, value, ParameterDescriptor(read_only=True))
        try:
            self.config = ReferenceConfig(**{name: self.get_parameter(name).value for name in
                ('subject_id', 'session_id', 'source_device_id', 'observation_type')})
        except ValueError:
            self.destroy_node()
            raise
        self.resolver = InMemoryReferenceResolver()
        self.publisher = self.create_publisher(
            ReferenceObservation, self.get_parameter('output_topic').value, 10)
        self.subscription = self.create_subscription(
            RawNotification, self.get_parameter('input_topic').value, self.on_raw, 10)

    def on_raw(self, message):
        try:
            observation = map_reference(message, self.config,
                                        self.subscription.topic_name, self.resolver)
        except ValueError as error:
            self.get_logger().warning(f'Rejecting invalid raw observation: {error}')
            return
        self.publisher.publish(observation)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = SensomativeReferenceAdapter()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

"""Subscribe and report structural validity, without interpreting human state."""

import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from ros4hc_observation_msgs.msg import HealthObservation

from .model import validate_observation


class ObservationValidator(Node):
    def __init__(self, **kwargs):
        super().__init__('observation_validator', **kwargs)
        self.declare_parameter(
            'input_topic', 'health_observations', ParameterDescriptor(read_only=True),
        )
        self.accepted_count = 0
        self.rejected_count = 0
        self.subscription = self.create_subscription(
            HealthObservation, self.get_parameter('input_topic').value, self.on_observation, 10,
        )

    def on_observation(self, message):
        errors = validate_observation(message)
        details = (
            f'type={message.observation_type} value={message.value} unit={message.unit!r} '
            f'unit_status={message.unit_status} '
            f'time={message.observation_time.sec}.{message.observation_time.nanosec:09d} '
            f'time_semantics={message.time_semantics} '
            f'source={message.source_device_type}/{message.source_device_id} '
            f'origin={message.source_topic}:{message.source_message_type}.{message.source_field} '
            f'data_level={message.data_level} validity={message.validity} '
            f'quality_available={message.quality_available} '
            f'subject={message.subject_id} session={message.session_id}'
        )
        if errors:
            self.rejected_count += 1
            self.get_logger().error('STRUCTURE_INVALID ' + '; '.join(errors) + ' | ' + details)
        else:
            self.accepted_count += 1
            self.get_logger().info('STRUCTURE_OK ' + details)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = ObservationValidator()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

"""Whole native notification reference mapping; no byte interpretation."""
from dataclasses import dataclass
from uuid import uuid4
import math

from ros4hc_observation_msgs.msg import ReferenceObservation, ObservationTime
from ros4hc_observation_tools.model import DataLevel, TimeSemantics, UnitStatus, Validity
from ros4hc_observation_tools.model_v02 import validate_reference

MESSAGE_TYPE = 'sensomative_raw_msgs/msg/RawNotification'
PROFILE = 'ros4hc/profile/sensomative-raw-notification/v1'


@dataclass(frozen=True)
class ReferenceConfig:
    subject_id: str
    session_id: str
    source_device_id: str = ''
    observation_type: str = 'sensomative_raw_payload'

    def __post_init__(self):
        for name in ('subject_id', 'session_id', 'observation_type'):
            if not getattr(self, name).strip():
                raise ValueError(f'{name} must be nonempty')


def epoch_time(nanoseconds, semantics, clock_domain, event_source):
    value = ObservationTime()
    sec, nanosec = divmod(nanoseconds, 1_000_000_000)
    if not -2147483648 <= sec <= 2147483647:
        raise ValueError('timestamp outside builtin_interfaces/Time range')
    value.stamp.sec, value.stamp.nanosec = sec, nanosec
    value.semantics = int(semantics)
    value.clock_domain, value.event_source = clock_domain, event_source
    return value


def map_reference(message, config, source_topic, resolver):
    """Return a validated ROS observation backed by this resolver instance.

    Zero optional stamps mean unavailable. Monotonic clocks stay native only.
    Validate metadata before registering to avoid storing rejected samples.
    """
    result = ReferenceObservation()
    m = result.metadata
    m.observation_id = str(uuid4())
    m.subject_id, m.session_id = config.subject_id, config.session_id
    m.observation_type = config.observation_type
    m.unit, m.unit_status = '', int(UnitStatus.UNKNOWN)
    m.data_level = int(DataLevel.RAW_SAMPLE)
    m.quality.validity = int(Validity.UNKNOWN)
    m.quality.quality_available = False
    m.quality.quality_value, m.quality.quality_scale = math.nan, ''
    m.observation_time = epoch_time(message.windows_received_utc_ns,
                                  TimeSemantics.HOST_RECEIVE, 'unix_utc',
                                  'windows_ble_callback')
    additional = []
    if message.wsl_received_utc_ns != 0:
        additional.append(epoch_time(message.wsl_received_utc_ns,
                                     TimeSemantics.HOST_RECEIVE, 'unix_utc',
                                     'wsl_tcp_frame_receive'))
    stamp = message.ros_publish_time
    if stamp.sec != 0 or stamp.nanosec != 0:
        additional.append(epoch_time(stamp.sec * 1_000_000_000 + stamp.nanosec,
                                     TimeSemantics.ROS_PUBLISH, 'raw_bridge_ros_clock',
                                     'raw_bridge_publish'))
    m.additional_times = additional
    m.source.device_type, m.source.device_id = 'sensomative', config.source_device_id
    m.source.topic, m.source.message_type = source_topic, MESSAGE_TYPE
    m.source.field_path, m.source.node = '$', ''
    # Structural validator does not consult the resolver.
    from ros4hc_observation_tools.model_v02 import validate_metadata
    errors = validate_metadata(m)
    if errors:
        raise ValueError('; '.join(errors))
    descriptor = resolver.register(message, MESSAGE_TYPE, profile=PROFILE)
    result.reference.scheme = descriptor.scheme
    result.reference.id = descriptor.id
    result.reference.profile = descriptor.profile
    errors = validate_reference(result)
    if errors:
        raise ValueError('; '.join(errors))
    return result

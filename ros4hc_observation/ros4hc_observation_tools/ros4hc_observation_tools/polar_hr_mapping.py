"""Map the existing Polar ROS HR output without inferring measurement validity."""

from dataclasses import dataclass
import math

from .model import (
    DataLevel, Observation, ObservationTime, TimeSemantics, UnitStatus, Validity,
    validate_observation,
)


@dataclass(frozen=True)
class PolarHRConfig:
    subject_id: str
    session_id: str
    source_device_id: str = ''

    def __post_init__(self):
        for name in ('subject_id', 'session_id'):
            if not getattr(self, name).strip():
                raise ValueError(f'{name} must be nonempty; set it at node startup')


def map_polar_hr(message, config, source_topic):
    """Accept an HR-shaped object; ROS imports are deliberately not required.

    The caller supplies its subscription's resolved topic. The Polar driver
    produces the incoming stamp immediately before publish; do not replace it
    with this adapter's current time. hr_quality and BLE contact state are not
    evidence available to this mapping and are never read.
    """
    observation = Observation(
        subject_id=config.subject_id,
        session_id=config.session_id,
        observation_type='heart_rate',
        value=float(message.hr),
        unit='bpm',
        unit_status=UnitStatus.VERIFIED,
        observation_time=ObservationTime(message.header.stamp.sec, message.header.stamp.nanosec),
        time_semantics=TimeSemantics.ROS_PUBLISH,
        source_device_type='polar_h10',
        source_device_id=config.source_device_id,
        source_topic=source_topic,
        source_message_type='ros4hc_msgs/msg/HR',
        source_field='hr',
        data_level=DataLevel.DEVICE_DERIVED,
        validity=Validity.UNKNOWN,
        quality_available=False,
        quality_value=math.nan,
        quality_scale='',
    )
    errors = validate_observation(observation)
    if errors:
        raise ValueError('; '.join(errors))
    return observation

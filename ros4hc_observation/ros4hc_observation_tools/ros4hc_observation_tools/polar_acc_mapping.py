"""ROS-independent Polar acceleration mapping; no rescaling or retiming."""

from dataclasses import dataclass, fields, is_dataclass
from enum import IntEnum
from uuid import uuid4

from .model import DataLevel, ObservationTime as Stamp, TimeSemantics, UnitStatus
from .model_v02 import (
    ObservationMetadata, ObservationQuality, ObservationSource, ObservationTime,
    Vector3, VectorObservation,
)


@dataclass(frozen=True)
class PolarAccConfig:
    subject_id: str
    session_id: str
    source_device_id: str = ''

    def __post_init__(self):
        for name in ('subject_id', 'session_id'):
            if not getattr(self, name).strip():
                raise ValueError(f'{name} must be nonempty; set it at node startup')


def map_polar_acc(message, config, source_topic):
    """Map one Vector3Stamped-shaped sample, without inferring missing metadata.

    source_topic is the subscription's resolved topic. UUID4 is an implementation
    choice, not a schema requirement. The caller validates before publishing.
    """
    return VectorObservation(
        metadata=ObservationMetadata(
            observation_id=str(uuid4()),
            subject_id=config.subject_id, session_id=config.session_id,
            observation_type='acceleration', unit='g', unit_status=UnitStatus.INFERRED,
            observation_time=ObservationTime(
                stamp=Stamp(message.header.stamp.sec, message.header.stamp.nanosec),
                semantics=TimeSemantics.ROS_PUBLISH,
            ),
            source=ObservationSource(
                device_type='polar_h10', device_id=config.source_device_id,
                topic=source_topic, message_type='geometry_msgs/msg/Vector3Stamped',
                field_path='vector',
            ),
            data_level=DataLevel.RAW_SAMPLE,
            quality=ObservationQuality(),
        ),
        value=Vector3(message.vector.x, message.vector.y, message.vector.z),
        frame_id=message.header.frame_id,
    )


def _copy_fields(source, target):
    """Copy the nested model into pre-created generated message fields."""
    for field in fields(source):
        value = getattr(source, field.name)
        if is_dataclass(value):
            _copy_fields(value, getattr(target, field.name))
        else:
            if isinstance(value, IntEnum):
                value = int(value)
            elif isinstance(value, tuple):
                value = list(value)
            setattr(target, field.name, value)


def to_vector_message(observation, message_type):
    message = message_type()
    _copy_fields(observation, message)
    return message

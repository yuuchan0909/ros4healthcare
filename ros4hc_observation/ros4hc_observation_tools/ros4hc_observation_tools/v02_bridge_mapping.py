"""Lossless v0.1 scalar schema migration, independent of ROS and node clocks."""

from uuid import uuid4

from .model import ObservationTime as Stamp
from .model_v02 import (
    ObservationMetadata, ObservationQuality, ObservationSource, ObservationTime,
    ScalarObservation,
)


def map_health_observation(message):
    """Accept a v0.1 model or generated message; preserve all original semantics.

    UUID4 is an implementation choice, not a v0.2 schema requirement.
    Validation belongs to the caller; invalid inputs are never repaired here.
    """
    return ScalarObservation(
        metadata=ObservationMetadata(
            observation_id=str(uuid4()),
            subject_id=message.subject_id,
            session_id=message.session_id,
            observation_type=message.observation_type,
            unit=message.unit,
            unit_status=message.unit_status,
            observation_time=ObservationTime(
                stamp=Stamp(message.observation_time.sec, message.observation_time.nanosec),
                semantics=message.time_semantics,
                clock_domain='', event_source='',
            ),
            additional_times=(),
            source=ObservationSource(
                device_type=message.source_device_type,
                device_id=message.source_device_id,
                topic=message.source_topic,
                message_type=message.source_message_type,
                field_path=message.source_field,
                node='',
            ),
            data_level=message.data_level,
            quality=ObservationQuality(
                validity=message.validity,
                quality_available=message.quality_available,
                quality_value=message.quality_value,
                quality_scale=message.quality_scale,
            ),
        ),
        value=message.value,
    )


def to_scalar_message(observation, message_type):
    """Copy the migrated model into a generated ScalarObservation."""
    message = message_type()
    message.value = observation.value
    source = observation.metadata
    target = message.metadata
    for name in ('observation_id', 'subject_id', 'session_id', 'observation_type',
                 'unit', 'unit_status', 'data_level'):
        setattr(target, name, getattr(source, name))
    for name in ('semantics', 'clock_domain', 'event_source'):
        setattr(target.observation_time, name, getattr(source.observation_time, name))
    target.observation_time.stamp.sec = source.observation_time.stamp.sec
    target.observation_time.stamp.nanosec = source.observation_time.stamp.nanosec
    target.additional_times = []
    for name in ('device_type', 'device_id', 'topic', 'message_type', 'field_path', 'node'):
        setattr(target.source, name, getattr(source.source, name))
    for name in ('validity', 'quality_available', 'quality_value', 'quality_scale'):
        setattr(target.quality, name, getattr(source.quality, name))
    return message

"""ROS-independent construction and structural validation; no physiological rules."""

from dataclasses import dataclass, field
from enum import IntEnum
import math


class UnitStatus(IntEnum):
    UNKNOWN = 0
    VERIFIED = 1
    INFERRED = 2


class TimeSemantics(IntEnum):
    UNKNOWN = 0
    DEVICE_MEASUREMENT = 1
    HOST_RECEIVE = 2
    ROS_PUBLISH = 3
    SYNTHETIC = 4


class DataLevel(IntEnum):
    UNKNOWN = 0
    RAW_SAMPLE = 1
    DEVICE_DERIVED = 2
    ROS_DERIVED = 3
    SYNTHETIC_SAMPLE = 4


class Validity(IntEnum):
    UNKNOWN = 0
    VALID = 1
    INVALID = 2
    MISSING = 3
    STALE = 4
    DUPLICATE = 5


@dataclass(frozen=True)
class ObservationTime:
    sec: int = 0
    nanosec: int = 0


@dataclass(frozen=True)
class Observation:
    subject_id: str
    session_id: str
    observation_type: str
    value: float
    unit: str = ''
    unit_status: int = UnitStatus.UNKNOWN
    observation_time: ObservationTime = field(default_factory=ObservationTime)
    time_semantics: int = TimeSemantics.UNKNOWN
    source_device_type: str = 'unknown'
    source_device_id: str = ''
    source_topic: str = ''
    source_message_type: str = ''
    source_field: str = ''
    data_level: int = DataLevel.UNKNOWN
    validity: int = Validity.UNKNOWN
    quality_available: bool = False
    quality_value: float = math.nan
    quality_scale: str = ''


def validate_observation(observation):
    """Return structural errors for a dataclass or generated ROS message.

    UNKNOWN is an accepted state, not proof that a measurement is valid.
    A successful check does not establish physiological or clinical validity.
    """
    errors = []
    for name in ('subject_id', 'session_id', 'observation_type',
                 'source_device_type', 'source_topic', 'source_message_type', 'source_field'):
        if not getattr(observation, name).strip():
            errors.append(f'{name} must be nonempty')
    for name, enum_type in (
        ('unit_status', UnitStatus), ('time_semantics', TimeSemantics),
        ('data_level', DataLevel), ('validity', Validity),
    ):
        if getattr(observation, name) not in {int(value) for value in enum_type}:
            errors.append(f'{name} has an unrecognized code')
    if observation.unit_status != UnitStatus.UNKNOWN and not observation.unit.strip():
        errors.append('a verified or inferred unit must be nonempty')
    stamp = observation.observation_time
    if not (-2147483648 <= stamp.sec <= 2147483647 and 0 <= stamp.nanosec < 1000000000):
        errors.append('observation_time is outside builtin_interfaces/Time range')
    if observation.time_semantics == TimeSemantics.UNKNOWN and (stamp.sec or stamp.nanosec):
        errors.append('unknown time requires a zero timestamp')
    if observation.validity == Validity.VALID and not math.isfinite(observation.value):
        errors.append('VALID requires a finite value')
    if observation.validity == Validity.MISSING and not math.isnan(observation.value):
        errors.append('MISSING requires a NaN value')
    if observation.quality_available:
        if not math.isfinite(observation.quality_value) or not observation.quality_scale.strip():
            errors.append('available quality requires a finite value and a scale')
    elif not math.isnan(observation.quality_value) or observation.quality_scale:
        errors.append('unavailable quality requires NaN and an empty scale')
    return errors


DEFAULT_VALUES = (72.0, 74.0, 75.0, 82.0, 95.0, 108.0, 112.0, 105.0, 96.0, 84.0, 76.0)


@dataclass(frozen=True)
class SyntheticConfig:
    values: tuple = DEFAULT_VALUES
    publish_period_sec: float = 1.0
    subject_id: str = 'synthetic_subject'
    session_id: str = 'synthetic_session'
    source_device_id: str = 'synthetic_hr_001'
    repeat: bool = False

    def __post_init__(self):
        if not self.values or any(not math.isfinite(value) for value in self.values):
            raise ValueError('values must be a nonempty finite numeric sequence')
        if not math.isfinite(self.publish_period_sec) or self.publish_period_sec <= 0:
            raise ValueError('publish_period_sec must be finite and positive')
        for name in ('subject_id', 'session_id', 'source_device_id'):
            if not getattr(self, name).strip():
                raise ValueError(f'{name} must be nonempty')


class SyntheticSequence:
    def __init__(self, config):
        self.config = config
        self.index = 0

    def next_observation(self, timestamp_ns, source_topic):
        """Build a sample using the caller's immediately pre-publish ROS clock reading."""
        if self.index >= len(self.config.values):
            if not self.config.repeat:
                return None
            self.index = 0
        sec, nanosec = divmod(timestamp_ns, 1000000000)
        observation = Observation(
            subject_id=self.config.subject_id,
            session_id=self.config.session_id,
            observation_type='heart_rate',
            value=float(self.config.values[self.index]),
            unit='bpm',
            unit_status=UnitStatus.VERIFIED,
            observation_time=ObservationTime(sec, nanosec),
            time_semantics=TimeSemantics.ROS_PUBLISH,
            source_device_type='synthetic',
            source_device_id=self.config.source_device_id,
            source_topic=source_topic,
            source_message_type='ros4hc_observation_msgs/msg/HealthObservation',
            source_field='value',
            data_level=DataLevel.SYNTHETIC_SAMPLE,
            validity=Validity.VALID,
        )
        errors = validate_observation(observation)
        if errors:
            raise ValueError('; '.join(errors))
        self.index += 1
        return observation


def to_ros_message(observation, message_type):
    """Copy every v0.1 field, preserving unknowns, NaN and timestamp semantics."""
    message = message_type()
    for name in Observation.__dataclass_fields__:
        if name != 'observation_time':
            value = getattr(observation, name)
            setattr(message, name, int(value) if isinstance(value, IntEnum) else value)
    message.observation_time.sec = observation.observation_time.sec
    message.observation_time.nanosec = observation.observation_time.nanosec
    return message

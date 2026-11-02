"""v0.2 contracts without ROS imports; validators also accept generated messages.

The v0.1 model, adapters and validator remain unchanged. Enum numbers are shared.
"""

from dataclasses import dataclass, field
import math

from .model import DataLevel, ObservationTime as Stamp, TimeSemantics, UnitStatus, Validity


@dataclass(frozen=True)
class ObservationTime:
    stamp: Stamp = field(default_factory=Stamp)
    semantics: int = TimeSemantics.UNKNOWN
    clock_domain: str = ''
    event_source: str = ''


@dataclass(frozen=True)
class ObservationQuality:
    validity: int = Validity.UNKNOWN
    quality_available: bool = False
    quality_value: float = math.nan
    quality_scale: str = ''


@dataclass(frozen=True)
class ObservationSource:
    device_type: str = 'unknown'
    device_id: str = ''
    topic: str = ''
    message_type: str = ''
    field_path: str = ''
    node: str = ''


@dataclass(frozen=True)
class ObservationMetadata:
    observation_id: str
    subject_id: str
    session_id: str
    observation_type: str
    unit: str = ''
    unit_status: int = UnitStatus.UNKNOWN
    observation_time: ObservationTime = field(default_factory=ObservationTime)
    additional_times: tuple = ()
    source: ObservationSource = field(default_factory=ObservationSource)
    data_level: int = DataLevel.UNKNOWN
    quality: ObservationQuality = field(default_factory=ObservationQuality)


@dataclass(frozen=True)
class ScalarObservation:
    metadata: ObservationMetadata
    value: float


@dataclass(frozen=True)
class Vector3:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class VectorObservation:
    metadata: ObservationMetadata
    value: Vector3
    frame_id: str = ''


def _nonempty(value, name, errors):
    if not isinstance(value, str) or not value.strip():
        errors.append(f'{name} must be nonempty')


def _enum(value, enum_type, name, errors):
    if isinstance(value, bool) or not isinstance(value, int) or value not in enum_type._value2member_map_:
        errors.append(f'{name} has an unrecognized code')


def validate_time(value):
    """Validate a timestamp without inferring clock domain or synchronization."""
    errors = []
    _enum(value.semantics, TimeSemantics, 'semantics', errors)
    stamp = value.stamp
    if (isinstance(stamp.sec, bool) or not isinstance(stamp.sec, int)
            or isinstance(stamp.nanosec, bool) or not isinstance(stamp.nanosec, int)
            or not -2147483648 <= stamp.sec <= 2147483647
            or not 0 <= stamp.nanosec < 1000000000):
        errors.append('stamp is outside builtin_interfaces/Time range')
    if value.semantics == TimeSemantics.UNKNOWN and (stamp.sec or stamp.nanosec):
        errors.append('unknown time requires a zero timestamp')
    for name in ('clock_domain', 'event_source'):
        if not isinstance(getattr(value, name), str):
            errors.append(f'{name} must be a string')
    return errors


def validate_quality(value):
    errors = []
    _enum(value.validity, Validity, 'validity', errors)
    if not isinstance(value.quality_available, bool):
        errors.append('quality_available must be bool')
    if not isinstance(value.quality_scale, str):
        errors.append('quality_scale must be a string')
    elif value.quality_available:
        if not math.isfinite(value.quality_value) or not value.quality_scale.strip():
            errors.append('available quality requires a finite value and a scale')
    elif not math.isnan(value.quality_value) or value.quality_scale:
        errors.append('unavailable quality requires NaN and an empty scale')
    return errors


def validate_metadata(value):
    """Stateless structural checks, never clinical checks or ID uniqueness checks."""
    errors = []
    for name in ('observation_id', 'subject_id', 'session_id', 'observation_type'):
        _nonempty(getattr(value, name), name, errors)
    _enum(value.unit_status, UnitStatus, 'unit_status', errors)
    _enum(value.data_level, DataLevel, 'data_level', errors)
    if not isinstance(value.unit, str):
        errors.append('unit must be a string')
    elif value.unit_status != UnitStatus.UNKNOWN and not value.unit.strip():
        errors.append('a verified or inferred unit must be nonempty')
    errors.extend('observation_time.' + error for error in validate_time(value.observation_time))
    for index, timestamp in enumerate(value.additional_times):
        errors.extend(f'additional_times[{index}].' + error for error in validate_time(timestamp))
    source = value.source
    for name in ('device_type', 'topic', 'message_type', 'field_path'):
        _nonempty(getattr(source, name), 'source.' + name, errors)
    for name in ('device_id', 'node'):
        if not isinstance(getattr(source, name), str):
            errors.append(f'source.{name} must be a string')
    # Keep source validation structural: no device registry or topic graph lookup.
    errors.extend('quality.' + error for error in validate_quality(value.quality))
    return errors


def _validate_values(values, validity):
    errors = []
    if any(math.isinf(component) for component in values):
        errors.append('infinite values are not allowed')
    if validity == Validity.VALID and not all(math.isfinite(component) for component in values):
        errors.append('VALID requires finite values for all components')
    if validity == Validity.MISSING and not all(math.isnan(component) for component in values):
        errors.append('MISSING requires NaN for all components')
    return errors


def validate_scalar(value):
    return validate_metadata(value.metadata) + _validate_values(
        (value.value,), value.metadata.quality.validity,
    )


def validate_vector(value):
    errors = validate_metadata(value.metadata)
    errors.extend(_validate_values(
        (value.value.x, value.value.y, value.value.z), value.metadata.quality.validity,
    ))
    if not isinstance(value.frame_id, str):
        errors.append('frame_id must be a string')
    return errors

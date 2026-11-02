# Observation v0.2 — Phase 1

Phase 1 adds common metadata, ScalarObservation, VectorObservation and pure
structural validators. HealthObservation v0.1, its enum numbers, topics,
Polar HR adapter and validator remain unchanged. Package version 0.2.0 adds
new type names; it does not replace the v0.1 wire schema. Existing entry points
remain v0.1. No v0.2 ROS node, acceleration adapter, ReferenceObservation,
resolver, provenance storage or DerivedFeature message is implemented here.

## Identity and source

`observation_id` is a nonempty opaque identifier, unique within the recording
collection or session. UUID, session + counter and recorder-assigned IDs are
all possible. The producer/recorder must ensure uniqueness; the stateless
validator checks nonemptiness only. It is not a device sample ID.

Subject/session/type are nonempty caller-assigned identifiers. Source device
type is nonempty (`unknown` allowed); device ID and node may be empty. Source
topic, canonical `package/msg/Type` and field path must be nonempty. Phase 1
checks these strings for presence, not graph existence or schema lookup.
Topic/type/field identify the input stream and field, not an individual sample.
There are no storage_id, record_id or provenance_id fields yet.

## Time

ObservationTime reuses every v0.1 time code without additions: UNKNOWN=0,
DEVICE_MEASUREMENT=1, HOST_RECEIVE=2, ROS_PUBLISH=3, SYNTHETIC=4.
UNKNOWN requires a zero stamp; known semantics may legitimately have zero time.
Negative seconds use normalized nonnegative nanoseconds. Clock domain and event
source may be empty when unavailable, even with known event semantics.

The primary time is source-associated; do not replace a source publish stamp
with adapter time and call it measurement time. `additional_times` contains
only available events. `event_source` identifies the timestamping point when
known. HOST_RECEIVE is not a license to label a ROS adapter callback as BLE
reception. A future adapter requiring a distinct ROS receive event must first
define its semantics; no speculative enum is introduced in Phase 1.

Do not infer synchronization, ordering or latency between different/unknown
clock domains. Do not reconstruct measurement time from nominal sample rate
and publish timestamps. No ordering, clock conversion or duplicate-event
constraint is imposed on additional_times in this phase.

## Units, quality and payload validity

Unit codes remain UNKNOWN=0, VERIFIED=1, INFERRED=2. A known unit status requires
a nonempty unit; UNKNOWN may preserve a supplied but unverified unit label.
No unit conversion, unit registry or physiological range validation is performed.
VERIFIED concerns the unit contract, not instrument accuracy.

Quality follows v0.1: unavailable requires NaN and an empty scale; available
requires a finite value and a nonempty native scale. Zero quality is available
when explicitly supplied. Native scales are not forced into [0,1]. Measurement
quality is not confidence in a human-state hypothesis. UNKNOWN validity is valid
metadata, not evidence of good sensor contact or physiological correctness.

Scalar VALID requires a finite value; MISSING requires NaN. Other validity states
allow finite or NaN. Vector is one simultaneous three-component sample with one
unit and one frame: VALID requires all components finite; MISSING requires all
components NaN. Other states may retain partially missing components as NaN,
without declaring individual axes valid. Infinity is rejected in both payloads.
An empty frame_id means unknown; never infer body/world/sensor frame.
There is no partial-validity mask or stale/packet-loss detector in Phase 1.

Generated ROS numeric defaults are zero, so a newly constructed
ObservationQuality must explicitly set quality_value to NaN when unavailable.
The pure Python model provides that default. Validators never repair or mutate
input values. Their accepted inputs are typed model objects or generated ROS
messages; they are not parsers for arbitrary untyped dictionaries.

## Future Polar mappings (contracts only)

| Field | HR → ScalarObservation | ACC → VectorObservation |
| --- | --- | --- |
| observation_type | heart_rate | acceleration |
| payload | float(source.hr) | copy source.vector.x/y/z unchanged |
| unit | bpm | g |
| unit_status | VERIFIED | INFERRED |
| primary stamp | source.header.stamp | source.header.stamp |
| semantics | ROS_PUBLISH | ROS_PUBLISH |
| device_type | polar_h10 | polar_h10 |
| source topic (default) | /polar_hr | /polar_acc |
| source message_type | ros4hc_msgs/msg/HR | geometry_msgs/msg/Vector3Stamped |
| source field_path | hr | vector |
| data_level | DEVICE_DERIVED | RAW_SAMPLE |
| validity | UNKNOWN | UNKNOWN |
| quality_available/value/scale | false / NaN / empty | false / NaN / empty |
| frame_id | not applicable | empty |

IDs/subject/session must be explicitly assigned. Device ID, source node,
clock_domain and event_source remain empty unless actually known.
Additional times remain empty unless separately recorded. Future adapters must
record resolved input topics when namespace/remapping changes the defaults.

Evidence: `ros4hc_observation_tools/ros4hc_observation_tools/polar_hr_mapping.py:32` implements the
v0.1 HR contract. `ros4hc_drv/polar_ros2/polar_ros2/submodules/PolarH10.py:138`
decodes device time internally, while lines 157–161 stamp and publish each
acceleration sample using the ROS clock and divide each axis by 1000.
The local driver does not declare its acceleration unit. Polar's official
[H10 specification](https://github.com/polarofficial/polar-ble-sdk/blob/master/documentation/products/PolarH10.md)
declares axis acceleration in mG; combined with the driver division, this
supports g as an inferred unit. It is deliberately not VERIFIED in Phase 1.
200 Hz is nominal driver configuration, not proof of measurement timestamps
or actual delivery rate. No extra scaling or inferred frame is applied.

`test_v02_contract.py` constructs future mapping examples from mock source
messages. They specify intended mappings; they do not test a nonexistent adapter.

## RAW_SAMPLE and conceptual layers

RAW_SAMPLE means a sensor sample preserving the meaning of its observed quantity,
not unprocessed bytes. Binary decoding, documented encoding scale, documented
unit conversion and specification-defined fixed ADC-to-physical-unit conversion
preserve RAW_SAMPLE. This does not certify absence of internal device filtering.

ROS-side calibration models, filtering, gravity removal, coordinate transformation,
aggregation and feature extraction produce ROS_DERIVED data. Device-computed
quantities such as HR are DEVICE_DERIVED. Synthetic samples remain explicitly
synthetic; transformations must not turn them into physical observations.

Processing level and conceptual layer are separate axes: feature extraction may
produce a DerivedFeature rather than an Observation. HumanStateHypothesis is
state interpretation. No feature or hypothesis types are added in this phase.

## Validation and testing

`model_v02.py` imports no ROS modules. `validate_metadata`, `validate_time`,
`validate_quality`, `validate_scalar` and `validate_vector` return structural error
lists and accept the pure model or corresponding generated message objects.
The v0.1 `model.py` and `validator.py` remain unchanged.

Pure tests, from repository root:

```sh
PYTHONPATH=ros4hc_observation/ros4hc_observation_tools python3 -m unittest discover -s ros4hc_observation/ros4hc_observation_tools/test -v
```

On Ubuntu 22.04 / ROS2 Humble, build both observation packages with their
dependencies available, source the resulting workspace and run colcon tests.
`test_v02_ros_interfaces.py` verifies real generated-message serialization;
it skips only if rclpy is absent. If ROS exists but v0.2 interfaces are not built
or sourced, imports fail rather than reporting a successful skipped check.
ID uniqueness, source-record resolution, synchronization and physiological
validity are deliberately outside these stateless validators.

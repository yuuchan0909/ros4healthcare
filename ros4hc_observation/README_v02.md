# Observation v0.2 — Phase 1 and Phase 2

Phase 1 provides common metadata, ScalarObservation, VectorObservation and pure
structural validators. Phase 2 adds ReferenceDescriptor, ReferenceObservation,
pure reference validators and a process-local in-memory reference fixture.
HealthObservation v0.1, existing interfaces, enum numbers, drivers, adapters and
bridge are unchanged by Phase 2. The implemented Polar HR/ACC paths and their
execution instructions are described in `ros4hc_observation_tools/README.md`.
No production storage/resolver or DerivedFeature message is added.

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
`validate_quality`, `validate_scalar`, `validate_vector`,
`validate_reference_descriptor` and `validate_reference` return structural error
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


## Phase 2: direct and reference payload contracts

- ScalarObservation = direct scalar payload.
- VectorObservation = direct simultaneous 3-component payload.
- ReferenceObservation = reference to one immutable native message instance or
  one producer-defined immutable block.

Use direct payloads when the meaning contract is clear and the direct type can
represent the sample without loss. Use references when native structure, block
boundaries or unresolved semantics need preservation. Size alone is not the
selection criterion. Reference does not imply RAW_SAMPLE: metadata.data_level
retains its existing meaning, including derived and synthetic data.

ReferenceObservation contains only metadata and a ReferenceDescriptor. The
latter has exactly scheme, id and profile. All three must be strings, nonempty
after strip; validation never strips or rewrites identifiers. There is no
hard-coded registry or restrictive identifier regex. Namespaced, versioned
contract identifiers such as `ros4hc/reference/in-memory/v1` and
`ros4hc/profile/native-message/v1` are recommended, not mandatory for validation.

### Identity, source and whole-message selection

- metadata.observation_id identifies the Observation Layer representation.
- metadata.source identifies the originating stream/message/field.
- reference.scheme + reference.id identifies the specific immutable native
  sample/block for retrieval. Consumers must not parse opaque IDs.
- reference.profile identifies the payload interpretation contract.

Different Observation IDs may share the same reference identity. A topic is not
an individual sample identity. A scheme specifies ID namespace, resolution
semantics and lifetime rules; it must not silently retarget an ID to new content.
Native session/sequence identifiers may participate in scheme-specific IDs, but
are not additional descriptor fields.

`metadata.source.message_type` is the canonical native ROS type returned by the
resolution layer. The resolver returns the whole native message, even when an
Observation targets a field. The reserved `metadata.source.field_path = "$"`
means the whole native message. Ordinary paths such as `hr`, `vector`, `payload`
retain their meaning; an empty field path remains invalid. The reference does
not duplicate source topic/type/field/device/node fields.

### Profiles and information preservation

A profile is an interpretation contract, not a nickname. Future profile
specifications may fix native schema, target field, ordering, layout,
unit/calibration status, timestamp mapping, unknown semantics and prohibited
inference. These remain outside the descriptor. A profile may explicitly state
that layout, units or encoding semantics are unresolved. Versioned scheme and
profile definitions must remain stable; firmware and native schema information
belongs in the native sample/profile/provenance rather than new common fields.

This is not a generic blob converting every payload to uint8[]. Native types are
preserved, schema/encoding responsibility is not moved to a custom blob decoder,
and Observation metadata is separated from payload retrieval identity.

### Structural validation versus resolution and measurement quality

validate_reference checks the existing metadata contract and descriptor string
presence only. It does not look up a resolver, test target existence or actual
ID uniqueness, retrieve payloads, recognize profiles, verify native schemas or
certify units/calibration or measurement validity. It is stateless and
non-mutating; typed pure models and generated message objects are supported.

resolved != measurement VALID; unresolved != measurement INVALID.

Resolution failure must not rewrite metadata.quality.validity. Transport
sequence gaps, retransmissions and audit information likewise must not be
inserted into measurement quality_value. Unsupported scheme, not found, expired,
timeout, access denied, schema mismatch and integrity failure are separate
resolution concepts. Phase 2 adds no ROS status/provenance message and does not
claim to detect all these failures.

### In-memory scheme and API

`in_memory_reference.py` implements `ros4hc/reference/in-memory/v1` strictly as
a ROS-independent contract fixture. Records exist only for the lifetime of one
resolver instance in one process; there is no persistence, expiry policy,
network access, cross-instance lookup or thread-safety guarantee.

```python
from ros4hc_observation_tools.in_memory_reference import InMemoryReferenceResolver

resolver = InMemoryReferenceResolver()
reference = resolver.register(native_sample, 'example_msgs/msg/Sample')
# Assign reference to a pure ReferenceObservation, or copy its three strings
# into a generated ReferenceDescriptor.
restored = resolver.resolve(reference, observation.metadata.source.message_type)
```

`register(payload, message_type, *, profile=NATIVE_PROFILE, reference_id=None)`
returns a pure ReferenceDescriptor. Default IDs use UUID4; callers and consumers
must treat them as opaque. Explicit recorder-assigned IDs are allowed, but any
existing ID is rejected, even when the new payload is equal. Registration
snapshots via deepcopy, and resolve returns another deepcopy, so ordinary native
objects cannot mutate stored content through input/output aliases. Payloads
must support faithful deepcopy; arbitrary custom copying hooks are not an
immutability/security guarantee. No generic byte serialization is performed.

The canonical message_type is asserted by the caller at registration, not
inferred or verified against ROS introspection. The fixture retains that type
identifier plus the Python native object. Resolve checks the expected identifier
from metadata.source.message_type against the stored identifier. It does not
certify that a caller supplied the correct ROS identifier for an arbitrary
Python object.

The default profile `ros4hc/profile/native-message/v1` means: retain the whole
registered native object and all its fields without conversion; `$` selects the
whole object and ordinary field paths select fields within it. Preserve native
ordering and block boundaries. Infer no units, calibration, geometry or device
measurement time. Existing metadata time/unit/quality rules apply; unavailable
semantics remain unknown. This generic profile does not pin an external schema
hash or certify firmware/schema compatibility. Stronger profiles may do so.

`resolve(reference, expected_message_type)` returns the native copy or raises
ReferenceResolutionError with a ResolutionFailure code: invalid_reference,
unsupported_scheme, not_found, type_mismatch or profile_mismatch. Profile equality
is checked against registration, not a global registry. No observation metadata
is read or mutated. A structurally valid descriptor may still fail resolution.

This fixture demonstrates sample recovery, not production durability or clinical
validity. Production storage, rosbag/database/network/file resolvers, Sensomative
and PressureMat adapters, Grid/Array, transport provenance messages,
DerivedFeature, HumanStateHypothesis, RAG and LLM integration remain out of scope.

### Phase 2 tests

`test_reference.py` checks descriptor/interface declarations, build registration,
whole-message `$`, metadata validation, unknown contracts, non-mutation, all data
levels, native sample recovery, ID reuse rejection, defensive copying, shared
references, type/profile mismatch and resolver-local lifetime.
`test_reference_ros_interfaces.py` tests real generated descriptor/observation
serialization and native geometry_msgs message recovery. It skips only when
rclpy is absent; installed ROS with missing/unbuilt new interfaces is a failure.
Run the existing full unittest discovery command above on Mac. On Humble rebuild
both observation packages, source the workspace, then run colcon tests and
inspect all results. A reference-only bag does not itself preserve the native
payload: recording/resolution guarantees require a separate implementation.

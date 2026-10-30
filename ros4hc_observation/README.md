# ROS4HC Observation Layer v0.1

Independent scalar-observation MVP for ROS 2 Humble. Existing ROS4HC drivers,
messages and launch files are unchanged. The new packages depend on ROS 2, not on
the existing device packages.

```text
Synthetic HR sequence (startup parameters)
  -> synthetic_hr
  -> ros4hc_observation_msgs/msg/HealthObservation
  -> /health_observations
  -> observation_validator (structural checks and logging)
```

Synthetic data tests connectivity and representation of the Observation Layer.
It does **not** evaluate physiological validity, diagnostic performance or human
state estimation. Baseline, trend, delta, activity/context interpretation, human
state/confidence, RAG, literature evidence and LLM outputs are not implemented.
Real-device adapters will be added later.

## Packages and build

- `ros4hc_observation_msgs`: `ament_cmake` / rosidl interface package.
- `ros4hc_observation_tools`: `ament_python` publisher, validator and tests.

The following is a **verification procedure**, not a record of a successful ROS
build. The development host has no ROS 2/rclpy/colcon; rosidl generation,
serialization and topic integration must still be run on Ubuntu 22.04 / ROS 2
Humble. Use a fresh terminal with the Humble installation already present.

Install the build/test dependencies on that Ubuntu host:

```bash
sudo apt update
sudo apt install python3-colcon-common-extensions python3-pytest python3-setuptools \
  ros-humble-ament-cmake ros-humble-rosidl-default-generators \
  ros-humble-builtin-interfaces ros-humble-rclpy ros-humble-rcl-interfaces \
  ros-humble-ros2cli ros-humble-ros2run ros-humble-ros2topic \
  ros-humble-ros2interface ros-humble-rmw-fastrtps-cpp
```

Assume the checkout is `~/ros2_ws/src/ros4healthcare`; adjust the workspace path
if needed. Build from the workspace root using isolated build/install directories:

```bash
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
colcon list --base-paths src/ros4healthcare/ros4hc_observation
colcon --log-base log_observation build \
  --base-paths src/ros4healthcare/ros4hc_observation \
  --build-base build_observation --install-base install_observation \
  --packages-select ros4hc_observation_msgs ros4hc_observation_tools --symlink-install
source install_observation/setup.bash
ros2 interface show ros4hc_observation_msgs/msg/HealthObservation
```

This restricted base path avoids discovering unrelated legacy packages or nested
duplicate packages elsewhere in ROS4HC. `colcon list` must show only the two
new packages. Check the build exit status and generated interface; seeing the
source `.msg` alone is not evidence of a successful build.

Run this setup in **every** terminal used below (publisher, validator, topic
inspection and tests):

```bash
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
source install_observation/setup.bash
export ROS_DOMAIN_ID=84
export ROS_LOCALHOST_ONLY=1
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
```

Domain 84 is an example: use an unused domain and the same settings in all
terminals. These commands limit discovery to the local host. Stop any daemon
started with different settings before inspecting topics (`ros2 daemon stop`).

Terminal A: start the subscriber **before** the publisher:

```bash
ros2 run ros4hc_observation_tools observation_validator --ros-args \
  -p input_topic:=/health_observations
```

Defaults: `[72, 74, 75, 82, 95, 108, 112, 105, 96, 84, 76]` bpm, one sample
per second. First sample is published after one period. After the last sample,
the timer stops on its next tick; the node remains alive until interrupted.
QoS uses depth 10, default reliable/volatile delivery: a late subscriber is not
guaranteed to receive the beginning. Start the validator first or use `repeat`.

Terminal B: start the publisher with explicit parameters (use floating point
literals for the double-array parameter):

```bash
ros2 run ros4hc_observation_tools synthetic_hr --ros-args \
  -p 'values:=[72.0, 105.0, 84.0]' \
  -p publish_period_sec:=0.5 \
  -p subject_id:=demo_subject \
  -p session_id:=demo_session \
  -p source_device_id:=generator_02 \
  -p output_topic:=/health_observations \
  -p repeat:=true
```

Terminal C: inspect discovery, endpoints, type and one sample:

```bash
ros2 topic list -t
ros2 topic info /health_observations --verbose
ros2 topic echo /health_observations ros4hc_observation_msgs/msg/HealthObservation --once
```

Expected checks (not results obtained on the development host):

- Topic type is `ros4hc_observation_msgs/msg/HealthObservation`, with a publisher
  and the validator subscriber visible in `topic info`.
- Values cycle through 72.0, 105.0, 84.0 with the requested identifiers and bpm.
- `unit_status=1`, `time_semantics=3` (ROS_PUBLISH), `data_level=4`
  (SYNTHETIC_SAMPLE), `validity=1`, `source_device_type=synthetic`.
- `source_topic=/health_observations`, source type is the canonical message type,
  and `source_field=value`.
- `quality_available=false`, `quality_value` is NaN (YAML may display `.nan`),
  `quality_scale` is empty. Timestamp is sampled before each publish, not a
  device acquisition time or an artificial timeline coordinate.
- Terminal A prints `STRUCTURE_OK` with the same metadata and no
  `STRUCTURE_INVALID`. This is structural validation, not physiological validity.

The manual example repeats so late CLI subscribers can still receive data.
After inspection, stop the publisher and validator with Ctrl+C before running
automated tests. Use a different unused ROS domain if other processes remain.

All publisher parameters (`values`, `publish_period_sec`, `subject_id`,
`session_id`, `source_device_id`, `repeat`, `output_topic`) are **startup-only,
read-only**. Restart the node to change them. This keeps subject/session and
sequence configuration consistent within a run. Runtime updates are rejected.
The validator's `input_topic` is also startup-only. Namespace/remapping are
supported; `source_topic` records the publisher's resolved output topic.

## Message contract

`msg/HealthObservation.msg` is the authoritative interface. ROS messages have no
enforced enum or optional scalar fields: prefixed `uint8` constants plus the
validator provide named states and reject unknown numeric codes. Strings remain
open for IDs, observation names, units, source paths and native quality scales.

| Fields | Types | Contract |
| --- | --- | --- |
| subject_id, session_id | string | Nonempty caller-assigned identifiers |
| observation_type | string | Nonempty; synthetic publisher uses `heart_rate` |
| value | float64 | Finite for VALID; NaN for MISSING |
| unit | string | `bpm` for synthetic HR; empty permitted for UNKNOWN unit status |
| unit_status | uint8 | UNKNOWN=0, VERIFIED=1, INFERRED=2 |
| observation_time | builtin_interfaces/Time | Source-associated time; interpretation is explicit |
| time_semantics | uint8 | UNKNOWN=0, DEVICE_MEASUREMENT=1, HOST_RECEIVE=2, ROS_PUBLISH=3, SYNTHETIC=4 |
| source_device_type, source_device_id | string | Type nonempty (`unknown` allowed); ID may be empty if unavailable |
| source_topic, source_message_type, source_field | string | Nonempty provenance: resolved topic, canonical ROS type, field path |
| data_level | uint8 | UNKNOWN=0, RAW_SAMPLE=1, DEVICE_DERIVED=2, ROS_DERIVED=3, SYNTHETIC_SAMPLE=4 |
| validity | uint8 | UNKNOWN=0, VALID=1, INVALID=2, MISSING=3, STALE=4, DUPLICATE=5 |
| quality_available | bool | Whether a native measurement-quality value actually exists |
| quality_value | float64 | NaN when unavailable; finite when available |
| quality_scale | string | Empty when unavailable; native scale description when available |

`SYNTHETIC_SAMPLE` avoids claiming a physical raw measurement or a physiological
algorithm-derived feature. Synthetic identity is additionally explicit in
`source_device_type=synthetic`. Data origin and timestamp meaning are independent.

Synthetic `unit_status=VERIFIED` means the generator's configured numeric values
are defined in bpm. It does not certify an instrument or physiological accuracy.
Synthetic `validity=VALID` means a finite, successfully generated sample; it does
not assert clinical validity. No physiological HR thresholds are imposed.

### Timestamp semantics

| Constant | Meaning of observation_time |
| --- | --- |
| TIME_UNKNOWN | No usable timestamp meaning is known; zero timestamp required |
| TIME_DEVICE_MEASUREMENT | Acquisition time supplied by a physical device |
| TIME_HOST_RECEIVE | Time the host received the original observation |
| TIME_ROS_PUBLISH | ROS clock reading immediately before publishing the observation |
| TIME_SYNTHETIC | Explicit coordinate on an artificially constructed timeline, e.g. sample index times a fixed interval |

The current publisher reads the ROS clock immediately before constructing and
publishing the message, so it emits **TIME_ROS_PUBLISH=3**. This is an approximate
pre-publish event time, not DDS transmission or subscriber reception time.
It does not emit TIME_SYNTHETIC; no artificial-timeline generator is implemented.
TIME_SYNTHETIC=4 is retained with the narrow meaning above, without renumbering
the interface. It is not a label for synthetic HR values.

With `use_sim_time=true`, the ROS clock follows `/clock`; this does not change
the event semantics to TIME_SYNTHETIC. Epoch, clock domain and multiple timestamps
are not encoded in v0.1 and must be recorded externally. DEVICE_MEASUREMENT times
from different devices cannot be compared without synchronization information.
Known semantics may legitimately have a zero timestamp at a simulation origin.
For negative times use normalized sec/nanosec, e.g. -0.5 s is sec=-1,
nanosec=500000000. Consumers must inspect semantics rather than treating zero
alone as unknown.

For direct synthetic production, provenance identifies the producer's **own**
output topic, `ros4hc_observation_msgs/msg/HealthObservation` and `value`. There is
no fictitious upstream sensor topic. A future device adapter should instead
record the original input topic/type/field; message field comments need no change.
This is minimal provenance, not a complete processing/version history.

Unknown metadata is accepted: UNKNOWN unit/time/data level/validity are valid
structural states; an unavailable device ID is empty and unavailable device type
is `unknown`. Subject/session and source path fields still must be nonempty.
There is no implied conversion from UNKNOWN to VALID or VERIFIED.

Quality is never synthesized: the publisher sends `false`, NaN and an empty
scale. Consumers must not turn that into zero quality or confidence. A newly
default-constructed ROS message has numeric zero defaults and is **not** a valid
unavailable-quality observation until its quality value is set to NaN.

The validator checks the NaN sentinel for contract consistency when quality is
unavailable; it does not use that value as a measurement-quality score or infer
validity from it. An available native quality value of zero is different from
unavailable quality. UNKNOWN enum codes are zero only in their own enum fields:
`value=0.0` can be VALID and `observation_time=0` can have known semantics.

### Structural review and remaining verification

- Constant **names** are globally unique. Values are unique within each enum
  group; zero and other values may repeat across different fields by design.
  All codes are in [0,255], so uint8 is sufficient. Tests enforce this.
- `builtin_interfaces/Time` matches the sec int32 / nanosec [0,1e9) representation
  and normalization contract. No clock domain is implied by the field alone.
- The Humble Python generator's floating-point setters allow NaN. This source
  inspection is not a local serialization result: generated-message round-trip
  and actual topic receipt of NaN are assertions in the pending ROS tests.
  Compare NaN with `math.isnan`, never equality; its binary payload is not part
  of the contract. JSON exports, if added later, will need an explicit policy.
- Source topic/type/field trace a ROS input or direct producer output, not an
  immutable packet or complete chain of processing. Identity/session mappings,
  versions, clock synchronization and intermediate processing remain external.
- SYNTHETIC_SAMPLE and RAW_SAMPLE have distinct codes. No schema expansion is
  needed to distinguish synthetic data from timestamp event semantics.

Primary references inspected for this review:
[Humble Time.msg](https://github.com/ros2/rcl_interfaces/blob/humble/builtin_interfaces/msg/Time.msg),
[Humble Python message generator](https://github.com/ros2/rosidl_python/blob/humble/rosidl_generator_py/resource/_msg.py.em).

The validator checks required strings, enum membership, time ranges/semantics,
finite VALID values, MISSING NaN values and quality consistency. It reports
`STRUCTURE_OK` or `STRUCTURE_INVALID` with type/value/unit/time/source/validity.
It does not infer missing metadata, normalize units, judge measurement quality,
enforce a closed observation vocabulary, detect stale/duplicate samples, or
interpret physiological state. STALE/DUPLICATE flags are representable only.

## Tests

ROS-independent tests need only Python's standard library:

```bash
cd src/ros4healthcare/ros4hc_observation/ros4hc_observation_tools
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  python3 -m unittest discover -s test -v
```

Without `rclpy`, three ROS integration tests are explicitly skipped. This does
not establish that rosidl generation or DDS delivery succeeds.

After building and sourcing ROS and both packages, run from the workspace root:

```bash
colcon --log-base log_observation test \
  --base-paths src/ros4healthcare/ros4hc_observation \
  --build-base build_observation --install-base install_observation \
  --packages-select ros4hc_observation_msgs ros4hc_observation_tools \
  --python-testing pytest \
  --event-handlers console_direct+ --return-code-on-test-failure
colcon test-result --test-result-base build_observation --verbose
```

Run the common terminal setup above first. The message package may have no
runtime tests of its own: its build generates the interface, and the tools
package's tests import and serialize that generated interface.

The test suite contains 9 ROS-independent tests and 3 ROS integration tests:

| Test group | What must execute on Humble |
| --- | --- |
| test_model.py | Sequence/config, unknowns, zero vs unknown, absent/present quality, time range/meaning, enum names/ranges/uniqueness and model/msg agreement |
| GeneratedMessageTest.test_generated_interface_and_serialization | Real generated message, serialize_message and deserialize_message, NaN, provenance and every timestamp semantic including artificial timeline |
| GeneratedMessageTest.test_unknowns_survive_wire_serialization | Unknown unit/time/validity and NaN values survive serialization |
| TopicPathTest | Real nodes and executor, parameter overrides and timer period, remapped resolved source topic, ROS_PUBLISH plus SYNTHETIC_SAMPLE, NaN after topic delivery, validator acceptance/rejection and read-only parameters |

On Humble, require all 12 tests to execute with **zero failures/errors and no ROS
integration skips**. Non-ROS runs explicitly skip 3 tests and cannot satisfy that
acceptance criterion. Inspect the summary and JUnit reports under
`build_observation/ros4hc_observation_tools`; an exit code alone does not establish
that skipped tests ran. These commands have not been executed on the non-ROS host.

A successful build verifies rosidl generation. Integration tests import the generated
interface, serialize/deserialize values (including NaN and UNKNOWN), instantiate
the real publisher with parameter overrides, verify the actual timer period,
resolve a remapped topic, receive observations over DDS and exercise validator
acceptance/rejection. If `rclpy` is installed but the generated message is absent,
the test fails instead of silently skipping. The local model test additionally
checks that message constants and field order match the Python model.

## Future Polar HR adapter

Add a separate executable/module subscribing to `ros4hc_msgs/msg/HR`, mapping
`hr` to `value` and preserving its header timestamp as ROS_PUBLISH semantics.
Record the original resolved topic/type/field, externally assign subject/session
and device identity, and use DEVICE_DERIVED data level. Do not assume that the
unpopulated `hr_quality=0` is measured quality, or invent a unit verification.
Verify the Polar protocol unit independently, then declare its status explicitly.
Add dependencies and adapter-specific tests at that time. Existing Polar startup
bugs and device operation remain outside this MVP; no driver repairs are included.

## License

New files follow the repository's GPL-3.0-or-later license; see `LICENSE`.

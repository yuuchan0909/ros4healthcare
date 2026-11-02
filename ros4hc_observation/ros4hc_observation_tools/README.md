# Polar HR adapter

The Observation Layer v0.1 baseline was verified on Ubuntu 22.04 / ROS 2 Humble
(two packages built, interface generated, 12 tests without failures or skips,
and synthetic publisher/validator communication), as reported by the user.
The new Polar adapter and its ROS integration tests still require Humble
verification. This adapter does not repair or launch the existing Polar driver.

```text
Existing Polar driver
  -> /polar_hr (ros4hc_msgs/msg/HR)
  -> polar_hr_adapter
  -> /health_observations (ros4hc_observation_msgs/msg/HealthObservation)
  -> observation_validator
```

The adapter is in `polar_hr_adapter.py`; the ROS-independent conversion is in
`polar_hr_mapping.py`. Neither the existing drivers nor either message package
is modified. The tools package now explicitly depends on `ros4hc_msgs`.

## Mapping contract

| Output | Mapping |
| --- | --- |
| subject_id, session_id | Nonempty startup parameters; no inferred identity |
| observation_type | `heart_rate` |
| value | `float(msg.hr)` |
| unit / unit_status | `bpm` / UNIT_STATUS_VERIFIED, per the Polar adapter contract |
| observation_time | Incoming header.stamp sec/nanosec copied exactly |
| time_semantics | TIME_ROS_PUBLISH: original Polar driver's pre-publish ROS clock reading |
| source_device_type | `polar_h10` |
| source_device_id | Startup parameter; empty when unknown/unavailable |
| source_topic | Subscription's resolved input topic, including namespace/remapping |
| source_message_type / source_field | `ros4hc_msgs/msg/HR` / `hr` |
| data_level | DATA_DEVICE_DERIVED: device-provided HR, not a raw ECG sample |
| validity | VALIDITY_UNKNOWN, regardless of the numeric value |
| quality_available / quality_value / quality_scale | false / NaN / empty string |

Unit verification is the specified mapping contract, not an instrument accuracy
claim; the legacy HR.msg itself does not carry a unit. Incoming hr_quality is
never read, including when it is zero or nonzero: the current Polar producer
does not populate it. BLE Sensor Contact flags are absent from the input, so
contact/quality cannot be reconstructed. No physiological thresholds are applied.
Even zero or negative HR remains UNKNOWN; receipt alone does not establish
validity. The original timestamp is never replaced with adapter receive/publish
time, and is never labeled DEVICE_MEASUREMENT.

Subject/session and source paths are structurally validated. A structurally
malformed input timestamp is logged and not emitted; no substitute timestamp or
physiological validity is invented. STRUCTURE_OK means the representation is
consistent, not that the measurement is physiologically valid.

## Build on Ubuntu / Humble

The baseline's two-package-only build instructions are insufficient for this
adapter unless `ros4hc_msgs` is already built and sourced. With the checkout in
`~/ros2_ws/src/ros4healthcare`, build these three packages in isolation:

```bash
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
colcon --log-base log_observation build \
  --base-paths src/ros4healthcare/ros4hc_msgs src/ros4healthcare/ros4hc_observation \
  --build-base build_observation --install-base install_observation \
  --packages-select ros4hc_msgs ros4hc_observation_msgs ros4hc_observation_tools \
  --symlink-install
source install_observation/setup.bash
```

Use the following environment setup in every terminal for testing:

```bash
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
source install_observation/setup.bash
export ROS_DOMAIN_ID=84
export ROS_LOCALHOST_ONLY=1
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
```

Choose an unused ROS domain. Stop synthetic_hr or other publishers to the same
output before testing the adapter. Start the validator in terminal A:

```bash
ros2 run ros4hc_observation_tools observation_validator
```

Terminal B:

```bash
ros2 run ros4hc_observation_tools polar_hr_adapter --ros-args \
  -p input_topic:=/polar_hr \
  -p output_topic:=/health_observations \
  -p subject_id:=participant_01 \
  -p session_id:=session_01
```

`source_device_id` defaults to empty, so its override can simply be omitted.
All five parameters are read-only/startup-only; restart to change them.
Subject/session default to empty and must be explicitly provided. This avoids
silently associating a real observation with an invented person or session.

Terminal C can publish a test HR message without BLE or a working Polar driver:

```bash
ros2 topic pub --once /polar_hr ros4hc_msgs/msg/HR \
  '{header: {stamp: {sec: 123, nanosec: 456789012}}, hr: 105, hr_quality: 0}'
ros2 topic info /health_observations --verbose
```

Expect STRUCTURE_OK with value=105.0, stamp=123.456789012, time_semantics=3,
data_level=2, validity=0 and quality_available=false. For topic echo, start echo
before publishing another input (the output is volatile):

```bash
ros2 topic echo /health_observations ros4hc_observation_msgs/msg/HealthObservation
```

This CLI fixture validates adapter mechanics; it is not a device-derived physical
measurement, even though it deliberately exercises the Polar-specific mapping.
Do not mix these fixtures into an actual participant's recording. Actual Polar
device acquisition remains subject to the unchanged driver's known limitations.

## Tests

Run the standard-library tests on a non-ROS host from this tools directory:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  python3 -m unittest discover -s test -v
```

Added tests: 9 ROS-independent mapping tests and 3 ROS integration tests.
The original 12 tests are unchanged. Expected discovery is 24 tests total:
18 execute without ROS and 6 ROS integration tests explicitly skip. Skips do not
prove ROS build, serialization or communication success.

On Humble, after the build and terminal setup above, stop manual test nodes and run:

```bash
colcon --log-base log_observation test \
  --base-paths src/ros4healthcare/ros4hc_msgs src/ros4healthcare/ros4hc_observation \
  --build-base build_observation --install-base install_observation \
  --packages-select ros4hc_observation_msgs ros4hc_observation_tools \
  --python-testing pytest \
  --event-handlers console_direct+ --return-code-on-test-failure
colcon test-result --test-result-base build_observation/ros4hc_observation_tools --verbose
```

Require 24 tools tests with zero errors, failures or skips. The added integration
tests check startup requirements, all five read-only parameters, remapped source
topics, real HR input and HealthObservation output over DDS, serialization,
exact timestamp preservation, identifiers and all semantic fields. They also
exercise zero/nonzero hr_quality and zero/negative HR without assigning validity.
Missing generated messages on a ROS host cause failure rather than silent skips.

No baseline, trend, context, state interpretation, RAG or LLM processing is added.

## v0.1 to v0.2 scalar bridge

`health_observation_v02_bridge` adds this migration path alongside the existing
v0.1 consumers. The Polar driver and Polar HR adapter are unchanged:

```text
/health_observations (HealthObservation)
  -> health_observation_v02_bridge
  -> /scalar_observations (ScalarObservation)
```

`v02_bridge_mapping.py` provides ROS-independent `map_health_observation` and
conversion to the generated ScalarObservation. The node subscribes/publishes
with queue depth 10 and validates the generated output using
`model_v02.validate_scalar` before publishing. Invalid observations produce a
warning, are dropped, and do not stop the node. Inputs are never repaired.
Only `input_topic` and `output_topic` are added as parameters; both are read-only
startup parameters with the defaults above.

| v0.2 output | v0.1 input / policy |
| --- | --- |
| metadata.observation_id | Fresh UUID4 per input callback |
| metadata.subject_id / session_id / observation_type | subject_id / session_id / observation_type |
| metadata.unit / unit_status | unit / unit_status, unchanged |
| value | value, including NaN |
| metadata.observation_time.stamp | observation_time, exact sec/nanosec |
| metadata.observation_time.semantics | time_semantics |
| metadata.observation_time.clock_domain / event_source | Empty strings; no inference |
| metadata.additional_times | Empty sequence (ROS message: `[]`) |
| metadata.source.device_type / device_id | source_device_type / source_device_id |
| metadata.source.topic / message_type / field_path | source_topic / source_message_type / source_field |
| metadata.source.node | Empty string; never the bridge node |
| metadata.data_level | data_level, unchanged |
| metadata.quality.validity | validity, unchanged |
| metadata.quality.quality_available / quality_value / quality_scale | quality_available / quality_value / quality_scale, including unknown/NaN |

The v0.2 schema itself does not require UUID format; this bridge implementation
uses UUID4 (`str(uuid.uuid4())`). The validator checks nonempty IDs without a
UUID-format requirement. Migration does not change the observation processing
level or original provenance, and adds no callback timestamp.

### Bridge verification

The bridge adds four ROS-independent mapping tests and two ROS integration tests.
The complete tools suite now discovers 43 tests (the existing 37 plus six).
On the development Mac, `python3 -m unittest discover -s test -v` passed 33 tests
with 10 ROS-dependent skips, zero errors and zero failures. `pytest` is not
installed on that host. ROS message generation and DDS integration remain to be
verified on Ubuntu 22.04 / ROS 2 Humble. Require 43 tools tests and no errors,
failures or skips there. Use the three-package build and colcon test commands
above; the current counts supersede the older adapter-only counts in this file.

The integration tests cover default topics, startup parameter overrides, DDS
input/output, exact timestamps, all metadata, nonempty IDs, validator success,
NaN delivery, invalid input rejection with warning, and continued publishing.
Generated interfaces missing on a ROS host fail rather than skip.

### Real Polar H10 check

Keep the working Polar driver running with its existing configuration and source
the same Humble/workspace environment in each terminal. If the existing Polar
adapter is already running, keep that instance. Otherwise start it:

```bash
ros2 run ros4hc_observation_tools polar_hr_adapter --ros-args \
  -p subject_id:=participant_01 -p session_id:=session_01
```

Start the bridge in another terminal:

```bash
ros2 run ros4hc_observation_tools health_observation_v02_bridge
# Optional startup topic overrides:
# ros2 run ros4hc_observation_tools health_observation_v02_bridge --ros-args \
#   -p input_topic:=/health_observations -p output_topic:=/scalar_observations
```

Observe both streams in separate terminals:

```bash
ros2 topic echo /health_observations ros4hc_observation_msgs/msg/HealthObservation
ros2 topic echo /scalar_observations ros4hc_observation_msgs/msg/ScalarObservation
```

Match corresponding samples by original timestamp and value. Confirm the copied
metadata, original Polar provenance, unchanged data_level/validity, empty new
provenance/time fields, no additional times, and nonempty observation_id. Existing
v0.1 consumers can continue subscribing to /health_observations.

## Polar acceleration: v0.2 VectorObservation path

```text
synthetic_polar_acc
  -> /polar_acc (geometry_msgs/msg/Vector3Stamped)
  -> polar_acc_adapter
  -> /vector_observations (ros4hc_observation_msgs/msg/VectorObservation)
```

The existing Polar driver, HR adapter, scalar bridge, message schemas and validators
are unchanged. `polar_acc_mapping.py` implements the ROS-independent mapping and
copies its nested dataclass model to a generated VectorObservation. The adapter
calls `model_v02.validate_vector` on that generated message before every publish.
Invalid samples are dropped with a warning; the node continues. Infinite values
are rejected; NaN with UNKNOWN validity follows the existing validator contract
and is preserved/accepted. No new validity rules or physiological checks are added.

| Output | Mapping / meaning |
| --- | --- |
| metadata.observation_id | New `str(uuid4())` for each sample; schema requires no UUID format |
| metadata.subject_id / session_id | Required startup parameters |
| metadata.observation_type | `acceleration` |
| value.x / y / z | input.vector.x / y / z, unchanged; no conversion to m/s² |
| metadata.unit / unit_status | `g` / INFERRED |
| metadata.observation_time.stamp | Exact input.header.stamp copy |
| metadata.observation_time.semantics | ROS_PUBLISH, never device measurement time |
| metadata.observation_time.clock_domain / event_source | Empty strings |
| metadata.additional_times | Empty sequence |
| metadata.source.device_type / device_id | `polar_h10` / source_device_id parameter |
| metadata.source.topic | Resolved subscription topic, after parameters/remapping |
| metadata.source.message_type / field_path | `geometry_msgs/msg/Vector3Stamped` / `vector` |
| metadata.source.node | Empty string, never the adapter node |
| metadata.data_level | RAW_SAMPLE |
| metadata.quality.validity | UNKNOWN |
| metadata.quality.quality_available / quality_value / quality_scale | false / NaN / empty string |
| frame_id | Exact input.header.frame_id, including empty string |

RAW_SAMPLE means a sensor sample retaining the meaning of the observed quantity,
not undecoded bytes. Binary decoding and documented scaling alone do not imply
ROS_DERIVED. The specified unit contract is INFERRED from Polar official
specifications together with the driver's `/1000` scaling; the local driver alone
does not state the unit. No VERIFIED unit claim is made.

Adapter parameters (all read-only/startup-only):

| Parameter | Default |
| --- | --- |
| input_topic | `/polar_acc` |
| output_topic | `/vector_observations` |
| subject_id / session_id | Empty; empty or whitespace-only values fail startup, matching HR adapter |
| source_device_id | Empty; permitted when unknown |

Synthetic publisher parameters (also read-only/startup-only):

| Parameter | Default |
| --- | --- |
| x / y / z | 0.10 / -0.05 / 0.98 |
| publish_rate_hz | 200.0 |
| output_topic | `/polar_acc` |

The publisher emits constant Vector3Stamped fixtures with an empty frame_id and
`get_clock().now().to_msg()` immediately before publish. Rate must be finite and
positive; xyz must be finite. The fixture only checks the adapter path's structure.
Although 200 Hz matches the existing driver configuration, it does not simulate
physical Polar behavior, timing jitter, BLE packet structure or packet arrival.
The adapter deliberately applies the same Polar mapping to these fixtures, so use
explicit synthetic subject/session identities to distinguish them from real data.

Both new nodes use depth 10 with the default ROS QoS, matching the existing driver
and adapters; no BEST_EFFORT override or independent QoS policy is introduced.

Limitations: actual Polar acceleration hardware remains unverified; device
measurement timestamps are not preserved by this public input message; no times
are reconstructed from the sample rate; frame is unknown when empty; quality
information is absent; `g` remains INFERRED. No performance/latency, CPU, memory,
long-duration loss or QoS optimization benchmark is included.

### Acceleration build and test on Ubuntu 22.04 / Humble

With ROS dependencies installed (including `ros-humble-geometry-msgs`):

```bash
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
colcon --log-base log_observation build \
  --base-paths src/ros4healthcare/ros4hc_msgs src/ros4healthcare/ros4hc_observation \
  --build-base build_observation --install-base install_observation \
  --packages-select ros4hc_msgs ros4hc_observation_msgs ros4hc_observation_tools \
  --symlink-install
source install_observation/setup.bash
export ROS_DOMAIN_ID=84
export ROS_LOCALHOST_ONLY=1
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp

# Stop manual test publishers/adapters; use an unused domain for automated tests.
colcon --log-base log_observation test \
  --base-paths src/ros4healthcare/ros4hc_msgs src/ros4healthcare/ros4hc_observation \
  --build-base build_observation --install-base install_observation \
  --packages-select ros4hc_observation_msgs ros4hc_observation_tools \
  --python-testing pytest \
  --event-handlers console_direct+ --return-code-on-test-failure
colcon test-result --test-result-base build_observation --verbose
```

Eight tests are added: four pure mapping tests and four ROS integration tests.
They cover every mapping field, identity requirements, immutable copies, fresh IDs,
empty/nonempty frames, all-axis inf/NaN contracts, startup parameters, resolved
remapped topics, DDS delivery and serialization, inf rejection with a warning and
subsequent delivery, and the synthetic publisher's configuration. The synthetic
E2E test uses the default 200 Hz (5 ms timer), waits for ten outputs and correlates
input/output stamps. This is a short operation check without throughput or loss
thresholds, not proof of sustained 200 Hz performance.

Current tools discovery: **51 tests** (existing 43 plus eight). On the Mac, 37
passed and 14 ROS-dependent tests skipped, with zero errors/failures, using:

```bash
cd ~/ros2_ws/src/ros4healthcare/ros4hc_observation/ros4hc_observation_tools
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" python3 -m unittest discover -s test -v
```

Humble execution of the new generated-message/DDS/200 Hz tests remains required.
Require all 51 tools tests to pass with zero errors, failures and skips there.
Missing generated interfaces on a ROS host cause failure rather than a skip.
These counts supersede earlier sections' historical totals.

### Synthetic E2E, three terminals

In **each** terminal first run:

```bash
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
source install_observation/setup.bash
export ROS_DOMAIN_ID=84
export ROS_LOCALHOST_ONLY=1
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
```

Terminal 1 — synthetic publisher:

```bash
ros2 run ros4hc_observation_tools synthetic_polar_acc --ros-args \
  -p x:=0.10 -p y:=-0.05 -p z:=0.98 \
  -p publish_rate_hz:=200.0 -p output_topic:=/polar_acc
```

Terminal 2 — adapter:

```bash
ros2 run ros4hc_observation_tools polar_acc_adapter --ros-args \
  -p input_topic:=/polar_acc -p output_topic:=/vector_observations \
  -p subject_id:=synthetic_subject -p session_id:=synthetic_session
```

Terminal 3 — output:

```bash
ros2 topic echo /vector_observations ros4hc_observation_msgs/msg/VectorObservation
```

Expect xyz=(0.10, -0.05, 0.98), acceleration, g/INFERRED, RAW_SAMPLE,
ROS_PUBLISH, UNKNOWN validity, unavailable quality, empty frame and fresh IDs.
Echo is an inspection tool, not a 200 Hz performance measurement.

For future hardware validation, stop synthetic_polar_acc and start the existing
Polar driver with acceleration enabled and its existing configuration. No code
or topic changes are needed; retain the adapter and output consumer. Restart the
adapter with the actual subject/session (and source_device_id if known) for the
real recording, rather than recording hardware under synthetic identities.

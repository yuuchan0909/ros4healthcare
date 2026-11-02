# Sensomative Windows → TCP → ROS 2 raw bridge

Preserves every byte of each BLE notification. No pressure, unit, sensor position,
calibration, sensor clock, or meaning of byte 13 is assigned. Payload length is
variable; 13 is only the current observed length. Existing `sensomative_ros`,
`PressureMat`, `/pressure1`, and Apple Watch packages are unchanged.

`sensomative_raw_msgs/msg/RawNotification` is a separate interface package next
to this package in `ros4hc_drv`. Standard byte-array messages cannot also retain
the session, UUID, independent clocks, payload length and acquisition sequence in
one atomic sample. Topic: **`/sensomative/raw`** (overridable with `--topic`).

## Build (WSL Ubuntu 22.04 / ROS 2 Humble)

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select sensomative_raw_msgs sensomative_raw_bridge --symlink-install
source install/setup.bash
mkdir -p ~/ros2_ws/data/sensomative/{raw,reports,bags}
```

Dependencies: ament_cmake, ament_python, rosidl_default_generators,
builtin_interfaces, rclpy, rosbag2_py, ros2bag and the sqlite3 rosbag storage plugin.
Windows needs Python 3.10+ and Bleak 3.x. WSL needs no Bluetooth controller.

## Windows dependencies

Use Windows PowerShell and a dedicated environment; no administrator rights or
driver changes are needed. Put the venv on a Windows local disk. Replace the
repository path below with the actual location; a WSL UNC path also works.

```powershell
python -m venv "$env:LOCALAPPDATA\sensomative-raw-venv"
$python = "$env:LOCALAPPDATA\sensomative-raw-venv\Scripts\python.exe"
$bridge = '\\wsl.localhost\Ubuntu-22.04\home\<WSL_USER>\ros2_ws\src\ros4healthcare\ros4hc_drv\sensomative_raw_bridge'
& $python -m pip install -r "$bridge\windows\requirements.txt"
```

Alternatively `--deps PATH` loads an existing isolated directory of Windows
Bleak/WinRT dependencies. The Windows script imports the adjacent pure-Python
protocol module; it does not import ROS. Preserve that directory structure if
copying the Windows scripts elsewhere.

## Start order and recording

1. Charge/power on the Sensomative and disconnect phone/vendor apps. Leave BLE
   advertising enabled. No pairing or custom characteristic writes are required
   by this receiver.
2. Start the WSL receiver with a new, unique log filename:

```bash
source ~/ros2_ws/install/setup.bash
ros2 run sensomative_raw_bridge tcp_receiver \
  --host 0.0.0.0 --port 8765 --require-subscriber \
  --log ~/ros2_ws/data/sensomative/raw/wsl_RUN.jsonl
```

3. In another sourced WSL terminal, start recording **only the raw topic**:

```bash
ros2 bag record --storage sqlite3 \
  -o ~/ros2_ws/data/sensomative/bags/sensomative_raw_YYYYMMDD_HHMMSS \
  /sensomative/raw
```

4. Start Windows BLE reception (PowerShell, variables from above):

```powershell
& $python "$bridge\windows\sensomative_ble_receiver.py" `
  --address "<BLE_ADDRESS>" `
  --uuid 000055c2-0000-1000-8000-00805f9b34fb `
  --host 127.0.0.1 --port 8765 `
  --log '\\wsl.localhost\Ubuntu-22.04\home\<WSL_USER>\ros2_ws\data\sensomative\raw\windows_RUN.jsonl'
```

For a locally coordinated 30-second trial, add `--seconds 30 --guided`. A Windows
GUI enables Start after BLE subscription. Clicking Start initiates a five-second
countdown, then 0–5 unloaded, 5–10 center, 10–15 release, 15–20 left,
20–25 right, 25–30 release. The instructions and capture use the same Windows
monotonic origin. The GUI does not measure the person's actual action times.
Without `--guided`, `--seconds N` begins immediately after subscription;
`--seconds 0` runs until Ctrl+C. No GUI is required in continuous mode.

The optional WSL orchestrator starts the receiver, waits for the bag subscriber,
launches the Windows guided trial, stops bag/receiver cleanly and runs verification:

```bash
source ~/ros2_ws/install/setup.bash
python3 ~/ros2_ws/src/ros4healthcare/ros4hc_drv/sensomative_raw_bridge/tools/run_guided_trial.py \
  --windows-python "/mnt/c/Users/<WINDOWS_USER>/AppData/Local/Programs/Python/Python313/python.exe" \
  --windows-deps ~/ros2_ws/data/sensomative/windows_deps \
  --address "<BLE_ADDRESS>"
```

Replace `<BLE_ADDRESS>`, `<WSL_USER>`, and `<WINDOWS_USER>` with your local values.
Both capture scripts require an explicit `--address` for BLE acquisition; there
is no device address default. `--replay-log` requires no BLE address.
Adjust Windows Python/dependency paths for your PC. `--data-root`, `--address`,
`--host` and `--port` are configurable. All run paths include a UTC timestamp;
existing runs are never overwritten.

Try Windows `127.0.0.1` first (WSL localhost forwarding). If that route does not
work, use the WSL IPv4 address from `hostname -I` with receiver `--host 0.0.0.0`.
No automatic firewall, portproxy, driver, administrator or passthrough changes
are made. Use on a trusted local connection: protocol v1 is not authenticated
or encrypted.

## Stop, reconnect and recover

Stop Windows first (Ctrl+C in continuous mode or automatic completion). Wait for
its summary: `pending_frames=0` and BLE notification count = TCP ACK count. Then
stop rosbag with Ctrl+C and allow it to flush metadata. Finally stop the WSL
receiver with Ctrl+C. Logs use exclusive creation and bags must use new paths.

BLE disconnect/error events are sent and audited. The Windows receiver retries
BLE discovery/connection after errors; the Windows application sequence continues
across BLE reconnects within one process. Notifications not delivered to the BLE
callback during disconnection cannot be recovered or counted. Finite capture
duration is wall time including disconnections, not a guarantee of continuous BLE.

TCP uses a 4-byte network-order length prefix, UTF-8 JSON, protocol version 1,
session UUID and transport frame ID. Raw bytes are a JSON integer array with an
explicit length. The receiver checks lengths, byte bounds, timestamps, UUID and
version. Frames over 1 MiB / payloads over 65535 bytes are rejected, not truncated.

Windows logs each original before enqueueing it. TCP sends one frame and waits
for an ACK, retries after disconnect, and retains unacknowledged frames in memory.
The same running WSL process suppresses a matching last-frame retransmission
(including an ACK-loss retry) without publishing again. Conflicting content or a
transport frame gap is rejected. BLE events and a two-second heartbeat use the
same framed transport but are not published to the raw topic.

WSL logs sequence gaps, duplicates, reversal, changing payload lengths, TCP
disconnects and BLE status events. These diagnostics appear on the console and in
the JSONL audit. Payload length changes are preserved, not filtered. A duplicate
or out-of-order *application sequence* is reported and preserved, not silently
discarded. Byte 13 is never used for sequencing.

ACK means the unique frame was handled and `publish()` returned; it is **not** a
durable rosbag write acknowledgement. Reliable QoS depth 4096 and
`--require-subscriber` avoid ordinary startup loss, but do not guarantee lossless
storage on process/OS/disk failure. The initial subscriber gate waits without
publishing until a subscriber exists. End-to-end verification is required for
every acquisition. TCP alone is not an end-to-end storage guarantee.

After a 60-second shutdown drain timeout the Windows summary reports remaining
frames; the original log remains available. The queue is not silently capped or
dropped; a prolonged outage consumes memory and disk. Logs are flushed per row
and the Windows log is fsynced at normal completion; power-loss durability during
capture is not guaranteed.

Deduplication state is in memory, **not persistent across WSL node restarts**.
After WSL restart do not continue midway through an old session. Stop Windows,
start a fresh WSL receiver and fresh bag, then replay the whole original log:

```powershell
& $python "$bridge\windows\sensomative_ble_receiver.py" `
  --host 127.0.0.1 --port 8765 --replay-log 'original_windows_RUN.jsonl' `
  --log 'new_replay_audit.jsonl'
```

Replay does not connect to BLE. It retains the original session, sequence, clocks,
UUID and bytes. Always replay to a fresh receiver/bag to avoid mixing sessions
or duplicating previously stored messages. Keep the original Windows log for
verification. The verifier below targets a normal acquisition, using its original
send/ACK audit; replay verification additionally requires its replay audit.

## Clocks and rate

The message stores Windows BLE callback UTC (`time.time_ns`, Unix epoch ns),
Windows callback monotonic (`perf_counter_ns`), WSL complete-frame receive UTC
and monotonic (`time.time_ns`, `monotonic_ns`), and ROS clock time sampled just
before publish. WSL receive is a complete application frame, not a TCP segment
timestamp. Host UTC clocks may differ or jump. Monotonic clocks have unrelated
origins and cannot be subtracted across hosts. No sensor-internal time is inferred.
BLE callback rate, WSL frame receive rate and publish rate are measured separately.

## Read back every bag message

After a normal acquisition and clean bag shutdown:

```bash
ros2 run sensomative_raw_bridge verify_bag \
  --windows-log ~/ros2_ws/data/sensomative/raw/windows_RUN.jsonl \
  --receiver-log ~/ros2_ws/data/sensomative/raw/wsl_RUN.jsonl \
  --bag ~/ros2_ws/data/sensomative/bags/sensomative_raw_YYYYMMDD_HHMMSS \
  --report ~/ros2_ws/data/sensomative/reports/verification_RUN.json
```

The verifier reads all messages using rosbag2_py, compares all payload bytes,
length, UUID, Windows sequence, session, frame ID and original Windows clocks in
order, compares counts and sequence lists across stages, and reports separate
rates. A mismatch returns exit code 1. It also rejects extra bag topics. Keep the
Windows original, receiver audit, verification report and bag together.

For a full-log recovery replay, pass the original acquisition as `--windows-log`,
the new replay sender audit as `--transport-log`, and the new receiver log/bag.

Protocol and receiver tests (no BLE access):

```bash
source ~/ros2_ws/install/setup.bash
cd ~/ros2_ws/src/ros4healthcare
ROS_LOCALHOST_ONLY=1 python3 -m unittest discover -s ros4hc_drv/sensomative_raw_bridge/test -v
```

The orchestrator also accepts `--replay-log /path/to/original_windows.jsonl`:
it creates a new receiver/bag, launches Windows in replay mode without BLE and
supplies both original and replay audits to the verifier automatically.

## Verified on 2026-10-22

Windows Python 3.13.0 / Bleak 3.0.2 / WinRT 3.2.1, WSL Ubuntu 22.04 / ROS 2
Humble, a Sensomative device (`<BLE_ADDRESS>`; device identifier omitted): a guided 30-second
acquisition produced 739 notifications, all 13 bytes. Windows original count,
unique TCP sends/ACKs, WSL receive count, ROS publish count and sqlite3 rosbag
count were all 739. All payload bytes, order, UUID, length, sequence and clocks
matched on bag readback. Callback/receive/publish rates were approximately
24.646 / 24.514 / 24.514 per second; these are host rates, not sensor sampling.
Windows localhost forwarding worked without network configuration changes.

Five automated tests cover fragmented/coalesced framing, invalid/truncated
frames, variable payload lengths, application sequence anomalies, ACK-loss
reconnection/retransmission and Windows callback copying. Altering one byte of
a copied original was detected at sequence 1. A separate whole-log Windows
replay into a fresh WSL receiver and fresh bag also preserved all 739 samples.
Unplanned physical BLE disconnect/reconnect during a capture was not forced.

The first acquisition attempt had no notifications because of an event argument
name collision; it was fixed before the successful trial. Its logs and empty
bag were retained separately, not overwritten. Successful live run ID:
`sensomative_raw_20261022_073857`; recovery replay:
`sensomative_raw_20261022_074203_replay`. Data are under
`~/ros2_ws/data/sensomative/{raw,bags,reports}` outside the repository.

## Relationship to the old driver / unknown specification

The existing driver reads only `data[0:12]`, publishes `PressureMat` to
`/pressure1`, and has a fixed MAC plus installation inconsistencies. This bridge
does not invoke it. Its topic/message/visualization might be reusable *after*
documented decoding is available, but currently the reason for dropping byte 13,
field widths, byte order, framing, physical layout, units, scaling, calibration
and firmware-dependent differences are unconfirmed. Obtain the manufacturer's
GATT/payload specification before adding a separate interpreted-data node.

## Native ReferenceObservation prototype

`sensomative_reference_adapter` subscribes to `sensomative_raw_msgs/msg/RawNotification`
and publishes `ros4hc_observation_msgs/msg/ReferenceObservation`. Mapping lives in
`reference_mapping.py`; ROS lifecycle and callbacks live in `reference_adapter.py`.
The existing Observation in-memory resolver is reused without schema changes.
No BLE, Windows receiver, TCP, ACK/retry, audit, guided trial, or bag verifier
behavior is changed.

All parameters are set at startup: `input_topic=/sensomative/raw`,
`output_topic=/reference_observations`, required nonempty `subject_id` and
`session_id`, `source_device_id=""`, and
`observation_type=sensomative_raw_payload`. Observation session identity describes
the study/recording; it never comes from the Windows capture session identity.

Metadata mapping:

| Field | Value |
| --- | --- |
| observation_id | Fresh UUID per notification, separate from reference ID and transport identities |
| subject_id / session_id | Parameters |
| observation_type | Parameter; default `sensomative_raw_payload` |
| unit / unit_status | Empty / UNKNOWN |
| data_level | RAW_SAMPLE |
| quality | UNKNOWN validity, unavailable, NaN value, empty scale |
| source.device_type / device_id | `sensomative` / parameter |
| source.topic | Resolved subscription topic, including ROS remapping |
| source.message_type / field_path / node | `sensomative_raw_msgs/msg/RawNotification` / `$` / empty |

RAW_SAMPLE here means an uninterpreted BLE notification is retained;
**one notification is not yet guaranteed to equal one physical sensor sample**.
Transport sequence or successful retransmission is not measurement validity.

Time mapping uses integer `divmod(ns, 1000000000)` into builtin_interfaces/Time.
The primary timestamp is `windows_received_utc_ns`, semantics HOST_RECEIVE,
clock_domain `unix_utc` (Unix epoch UTC host clock), event_source
`windows_ble_callback` (Windows BLE notification callback host reception).
Additional times preserve nonzero `wsl_received_utc_ns` as HOST_RECEIVE,
`unix_utc`, `wsl_tcp_frame_receive` (complete TCP frame received by WSL), and
nonzero `ros_publish_time` as ROS_PUBLISH, `raw_bridge_ros_clock`,
`raw_bridge_publish` (raw bridge's clock sampled immediately before publishing).
`raw_bridge_ros_clock` does not assert UTC: ROS time may use simulation time.
Zero optional timestamps are treated as unavailable; zero primary UTC is retained
as Unix epoch rather than replaced by adapter time. Host clock synchronization
and latency are not guaranteed. Out-of-Time-range timestamps are rejected.
Windows and WSL monotonic clocks remain only inside the native message; their
origins differ and they are never converted to ObservationTime.

### Reference contract and local recovery

Scheme: `ros4hc/reference/in-memory/v1` (existing resolver's exact scheme).
Profile: `ros4hc/profile/sensomative-raw-notification/v1`.
This namespaced, versioned profile is a documentation contract:

- Native type is `sensomative_raw_msgs/msg/RawNotification`, whole message retained.
- Payload bytes and byte ordering are unchanged, with every native field preserved.
- Unit, byte-to-cell mapping, physical geometry, calibration, and device timestamp are unknown.
- Only host timing is identified; no unsupported inference is performed.

The mapper validates metadata, calls
`resolver.register(message, MESSAGE_TYPE, profile=PROFILE)`, uses its returned
opaque descriptor, then runs the existing `validate_reference` before publication.
Invalid metadata produces a warning, no publication, and continued node operation.
Validation never invokes resolution. Existing resolver deepcopy on registration
and on resolution isolates both the incoming message and returned native copies.

In the same process, with the **same live adapter instance**:

```python
original = adapter.resolver.resolve(
    observation.reference, observation.metadata.source.message_type)
# original is a complete, isolated RawNotification native message.
```

The resolver is process-local and instance-local. A separate subscriber only gets
the descriptor and cannot access this store. Restart loses all registrations.
There is no persistent storage, cross-process resolution, expiry, capacity limit,
or thread-safety guarantee. The prototype's append-only store grows with input;
use bounded experimental runs. This verifies native register → observation →
same-process resolve → complete native recovery, not a production Reference system.

### Build, tests, and synthetic ROS E2E

From `~/ros2_ws` (normal local build):

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
colcon build --packages-select ros4hc_observation_msgs ros4hc_observation_tools sensomative_raw_bridge
source install/setup.bash
cd src/ros4healthcare
ROS_LOCALHOST_ONLY=1 python3 -m unittest discover -s ros4hc_drv/sensomative_raw_bridge/test -v
ROS_LOCALHOST_ONLY=1 python3 -m unittest discover -s ros4hc_observation/ros4hc_observation_tools/test -v
```

For each manual terminal source `~/ros2_ws/install/setup.bash` after
building. Use the same ROS_DOMAIN_ID and ROS_LOCALHOST_ONLY setting in all
terminals. The commands below use the normal workspace install.

Terminal 1:

```bash
ros2 run sensomative_raw_bridge sensomative_reference_adapter --ros-args \
  -p subject_id:=synthetic-subject -p session_id:=study-recording \
  -p source_device_id:=synthetic-device
```

Terminal 2 (start before publishing):

```bash
ros2 topic echo /reference_observations ros4hc_observation_msgs/msg/ReferenceObservation --once
```

Terminal 3:

```bash
ros2 topic pub --once /sensomative/raw sensomative_raw_msgs/msg/RawNotification \
  '{protocol_version: 1, session_id: windows-capture, transport_frame_id: 7, windows_sequence: 9, windows_received_utc_ns: 1700000000123456789, windows_monotonic_ns: 123, wsl_received_utc_ns: 1700000001123456789, wsl_monotonic_ns: 456, ros_publish_time: {sec: 1700000001, nanosec: 234567890}, characteristic_uuid: synthetic, payload_length: 4, payload: [0, 1, 127, 255]}'
```

This manual E2E confirms generation only; a separate terminal cannot resolve the
reference. The integration test additionally resolves using the adapter's actual
resolver in its own process and compares the whole native message.

For real Sensomative reception, replace the synthetic publisher with the existing
raw bridge and choose the real topic/device/study identities. Mapping, unknown
units/quality, native preservation and resolver limitations stay the same.
No pressure grid, physical units or calibrated interpretation is established.

A production resolver needs a shared lookup protocol, persistent native message
serialization plus schema/type versioning, durable opaque IDs and namespace,
profile compatibility, access control, retention/capacity policy, concurrency,
restart/recovery behavior, and explicit resolution failure semantics. These are
separate from measurement quality and do not justify sensor interpretation.

Validation performed on Ubuntu 22.04.5 / ROS2 Humble, branch
`human-state-interpretation`: isolated colcon build of Observation messages,
Observation tools and raw bridge completed (3 packages). Raw bridge suite:
12 tests (7 new), 0 errors, 0 failures, 0 skipped. Existing Observation suite:
66 tests, 0 errors, 0 failures, 0 skipped. Synthetic ROS integration passed;
no physical BLE device was used. Logs: `/tmp/sensomative-reference-tests.log`
and `/tmp/sensomative-observation-tests.log`. Historical, disposable validation build/install artifacts were under
`/tmp/sensomative-reference-{build,install}`. Those temporary paths are not
required or used by the normal build and E2E instructions above. No tracked source changes or Git
index operations were performed; raw bridge and raw messages remain untracked.

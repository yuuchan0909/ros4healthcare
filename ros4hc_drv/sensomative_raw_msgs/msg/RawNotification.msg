# No sensor timestamp, pressure unit, scaling or byte interpretation is implied.
uint16 protocol_version
string session_id
uint64 transport_frame_id
uint64 windows_sequence
# Host clocks: UTC Unix epoch and Windows perf_counter_ns (different origins).
int64 windows_received_utc_ns
uint64 windows_monotonic_ns
# WSL time.time_ns immediately after a complete TCP frame arrives.
int64 wsl_received_utc_ns
uint64 wsl_monotonic_ns
# ROS clock sampled immediately before publish(); may use simulated time.
builtin_interfaces/Time ros_publish_time
string characteristic_uuid
uint32 payload_length
uint8[] payload

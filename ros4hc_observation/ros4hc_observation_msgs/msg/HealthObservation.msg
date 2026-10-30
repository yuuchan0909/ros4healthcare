# Scalar observation v0.1. No interpretation or history-derived features.
uint8 UNIT_STATUS_UNKNOWN=0
uint8 UNIT_STATUS_VERIFIED=1
uint8 UNIT_STATUS_INFERRED=2

uint8 TIME_UNKNOWN=0
uint8 TIME_DEVICE_MEASUREMENT=1
uint8 TIME_HOST_RECEIVE=2
uint8 TIME_ROS_PUBLISH=3
# Timestamp is a coordinate on an explicitly artificial timeline, not merely synthetic data.
uint8 TIME_SYNTHETIC=4

uint8 DATA_UNKNOWN=0
uint8 DATA_RAW_SAMPLE=1
uint8 DATA_DEVICE_DERIVED=2
uint8 DATA_ROS_DERIVED=3
uint8 DATA_SYNTHETIC_SAMPLE=4

uint8 VALIDITY_UNKNOWN=0
uint8 VALIDITY_VALID=1
uint8 VALIDITY_INVALID=2
uint8 VALIDITY_MISSING=3
uint8 VALIDITY_STALE=4
uint8 VALIDITY_DUPLICATE=5

# Nonempty, caller-assigned identifiers. Not inferred from sensor measurements.
string subject_id
string session_id
string observation_type
# VALID requires a finite value. MISSING uses NaN; other non-valid states may use NaN.
float64 value
# Empty is allowed only with UNIT_STATUS_UNKNOWN.
string unit
uint8 unit_status

# Meaning is given by time_semantics; not implicitly device time or wall time.
# DEVICE_MEASUREMENT: acquisition time supplied by the physical device.
# HOST_RECEIVE: time the host received the original observation.
# ROS_PUBLISH: ROS clock sampled immediately before publishing the observation.
# SYNTHETIC: explicitly constructed timeline coordinate (not implemented by this publisher).
# TIME_UNKNOWN uses zero sec/nanosec. Clock domain/epoch is not encoded in v0.1.
builtin_interfaces/Time observation_time
uint8 time_semantics

# Device ID may be empty when unavailable; type must name a source or be "unknown".
string source_device_type
string source_device_id
# Resolved ROS topic, canonical package/msg/Type, and source field path.
# For direct synthetic production these identify this publisher's own output.
string source_topic
string source_message_type
string source_field
uint8 data_level
uint8 validity

# False means unavailable: quality_value MUST be NaN and quality_scale empty.
# True requires a finite value and a nonempty description of the native scale.
# This is measurement quality, never human-state confidence.
bool quality_available
float64 quality_value
string quality_scale

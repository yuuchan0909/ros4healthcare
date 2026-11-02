uint8 UNIT_STATUS_UNKNOWN=0
uint8 UNIT_STATUS_VERIFIED=1
uint8 UNIT_STATUS_INFERRED=2

uint8 DATA_UNKNOWN=0
uint8 DATA_RAW_SAMPLE=1
uint8 DATA_DEVICE_DERIVED=2
uint8 DATA_ROS_DERIVED=3
uint8 DATA_SYNTHETIC_SAMPLE=4

# Nonempty opaque ID, unique within the recording collection or session.
# Uniqueness is a producer/recorder responsibility, not a stateless validation.
string observation_id
string subject_id
string session_id
string observation_type
# Empty unit is allowed only with UNKNOWN status; no automatic conversions.
string unit
uint8 unit_status
ObservationTime observation_time
# Only actually available additional event times; never invented measurements.
ObservationTime[] additional_times
ObservationSource source
uint8 data_level
ObservationQuality quality

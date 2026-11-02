ObservationMetadata metadata
# One simultaneous 3-component sample with one shared unit.
# VALID requires all finite; MISSING requires all NaN.
# Other states allow finite/NaN components. Infinity is never allowed.
geometry_msgs/Vector3 value
# Empty means unknown. Never infer sensor/body/world frame from the payload.
string frame_id

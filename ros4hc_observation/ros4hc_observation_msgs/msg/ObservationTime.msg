uint8 TIME_UNKNOWN=0
uint8 TIME_DEVICE_MEASUREMENT=1
uint8 TIME_HOST_RECEIVE=2
uint8 TIME_ROS_PUBLISH=3
uint8 TIME_SYNTHETIC=4

# UNKNOWN requires zero stamp. Empty domain/source means unavailable.
# Known semantics may have a zero stamp; domain does not imply synchronization.
# ROS_PUBLISH identifies the publisher named by event_source when known.
builtin_interfaces/Time stamp
uint8 semantics
string clock_domain
string event_source

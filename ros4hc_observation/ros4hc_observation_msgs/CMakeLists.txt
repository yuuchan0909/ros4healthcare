cmake_minimum_required(VERSION 3.8)
project(ros4hc_observation_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(geometry_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/HealthObservation.msg"
  "msg/ObservationTime.msg"
  "msg/ObservationQuality.msg"
  "msg/ObservationSource.msg"
  "msg/ObservationMetadata.msg"
  "msg/ScalarObservation.msg"
  "msg/VectorObservation.msg"
  DEPENDENCIES builtin_interfaces geometry_msgs
)
ament_export_dependencies(rosidl_default_runtime)
ament_package()

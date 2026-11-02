"""合成入力の作成と既存mapping APIの呼び出し。意味変換は実装しない。"""
import math
from geometry_msgs.msg import Vector3Stamped
from ros4hc_msgs.msg import HR
from ros4hc_observation_msgs.msg import ScalarObservation, VectorObservation
from sensomative_raw_msgs.msg import RawNotification
from ros4hc_observation_tools.polar_hr_mapping import PolarHRConfig, map_polar_hr
from ros4hc_observation_tools.polar_acc_mapping import PolarAccConfig, map_polar_acc, to_vector_message
from ros4hc_observation_tools.v02_bridge_mapping import map_health_observation, to_scalar_message
from ros4hc_observation_tools.model_v02 import validate_scalar, validate_vector, validate_reference
from ros4hc_observation_tools.in_memory_reference import InMemoryReferenceResolver
from sensomative_raw_bridge.reference_mapping import ReferenceConfig, map_reference

CASE_NAMES = ('polar_hr', 'polar_acc', 'sensomative_raw')
SOURCE_TYPES = ('ros4hc_msgs/msg/HR', 'geometry_msgs/msg/Vector3Stamped',
                'sensomative_raw_msgs/msg/RawNotification')


def make_sources(case, samples):
    values = []
    for index in range(samples):
        if case == 'polar_hr':
            value = HR(hr=40 + index % 141, hr_quality=0)
            value.header.stamp.sec, value.header.stamp.nanosec = 1700000000, index % 1000000000
        elif case == 'polar_acc':
            value = Vector3Stamped()
            value.header.stamp.sec, value.header.stamp.nanosec = 1700000000, index % 1000000000
            value.header.frame_id = '' if index % 2 == 0 else 'synthetic_frame'
            value.vector.x = (index % 201 - 100) / 100.0
            value.vector.y = math.nan if index % 11 == 10 else -(index % 101) / 100.0
            value.vector.z = 0.98
        elif case == 'sensomative_raw':
            length = (13, 0, 5, 32)[index % 4]
            value = RawNotification(protocol_version=1, session_id='synthetic-capture',
                transport_frame_id=index + 1, windows_sequence=index + 17,
                windows_received_utc_ns=1700000000123456789 + index,
                windows_monotonic_ns=10000 + index,
                wsl_received_utc_ns=1700000001123456789 + index,
                wsl_monotonic_ns=20000 + index, characteristic_uuid='synthetic-characteristic',
                payload_length=length, payload=[(index + j) % 256 for j in range(length)])
            value.ros_publish_time.sec, value.ros_publish_time.nanosec = 1700000001, index % 1000000000
        else:
            raise ValueError('未対応の評価ケース: ' + case)
        values.append(value)
    return values


class MappingCase:
    """設定とresolverの作成は計測外。Referenceは実際の登録を含む。"""
    def __init__(self, name):
        self.name = name
        self.resolver = InMemoryReferenceResolver() if name == 'sensomative_raw' else None
        config_type = {'polar_hr': PolarHRConfig, 'polar_acc': PolarAccConfig,
                       'sensomative_raw': ReferenceConfig}[name]
        self.config = config_type('synthetic-subject', 'synthetic-study', 'synthetic-device')
        self.validator = {'polar_hr': validate_scalar, 'polar_acc': validate_vector,
                          'sensomative_raw': validate_reference}[name]

    def map(self, source):
        if self.name == 'polar_hr':
            return map_health_observation(map_polar_hr(source, self.config, '/synthetic/polar_hr'))
        if self.name == 'polar_acc':
            return map_polar_acc(source, self.config, '/synthetic/polar_acc')
        return map_reference(source, self.config, '/synthetic/sensomative/raw', self.resolver)

    def generated(self, observation):
        if self.name == 'polar_hr':
            return to_scalar_message(observation, ScalarObservation)
        if self.name == 'polar_acc':
            return to_vector_message(observation, VectorObservation)
        return observation

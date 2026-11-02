"""Synthetic actual adapter mappings; no node, hardware, or networking needed."""
from types import SimpleNamespace as NS

from ros4hc_observation_tools.in_memory_reference import InMemoryReferenceResolver
from ros4hc_observation_tools.polar_hr_mapping import PolarHRConfig, map_polar_hr
from ros4hc_observation_tools.v02_bridge_mapping import map_health_observation
from ros4hc_observation_tools.polar_acc_mapping import PolarAccConfig, map_polar_acc
from .evaluation import (
    evaluate_reference, evaluate_scalar, evaluate_vector, evaluation_report, report_json,
)


def synthetic_raw():
    from sensomative_raw_msgs.msg import RawNotification
    value = RawNotification(
        protocol_version=1, session_id='synthetic-windows-capture',
        transport_frame_id=17, windows_sequence=23,
        windows_received_utc_ns=1700000000123456789, windows_monotonic_ns=12345,
        wsl_received_utc_ns=1700000001123456789, wsl_monotonic_ns=67890,
        characteristic_uuid='synthetic-characteristic', payload_length=5,
        payload=[0, 1, 127, 128, 255])
    value.ros_publish_time.sec, value.ros_publish_time.nanosec = 1700000001, 123456790
    return value


def sensomative_case(raw=None):
    """Use the actual production mapper and existing process-local resolver."""
    from sensomative_raw_bridge.reference_mapping import ReferenceConfig, map_reference
    raw = synthetic_raw() if raw is None else raw
    resolver = InMemoryReferenceResolver()
    observation = map_reference(raw, ReferenceConfig('synthetic-subject', 'synthetic-study'),
                                '/synthetic/sensomative/raw', resolver)
    report = evaluation_report(observation)
    report['reference_recovery'] = {'evaluated': True,
                                    **evaluate_reference(observation, raw, resolver)}
    return report


def demo_report():
    stamp = NS(sec=123, nanosec=456)
    hr = NS(header=NS(stamp=stamp), hr=72)
    scalar = map_health_observation(map_polar_hr(
        hr, PolarHRConfig('synthetic-subject', 'synthetic-study'), '/synthetic/polar_hr'))
    acc = NS(header=NS(stamp=stamp, frame_id=''), vector=NS(x=0.1, y=-0.05, z=0.98))
    vector = map_polar_acc(acc, PolarAccConfig('synthetic-subject', 'synthetic-study'),
                           '/synthetic/polar_acc')
    scalar_report, vector_report = evaluation_report(scalar), evaluation_report(vector)
    scalar_report['value_preservation'] = {'evaluated': True, **evaluate_scalar(scalar, hr.hr)}
    vector_report['value_preservation'] = {'evaluated': True,
                                         **evaluate_vector(vector, acc.vector, acc.header.frame_id)}
    return {'report_version': 'phase4a/v1', 'synthetic_only': True,
            'cases': [scalar_report, vector_report, sensomative_case()]}


def main():
    print(report_json(demo_report()))

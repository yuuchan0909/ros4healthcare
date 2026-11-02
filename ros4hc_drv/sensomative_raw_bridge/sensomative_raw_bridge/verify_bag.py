"""Read every ROS message back and compare to the Windows BLE original."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

import rosbag2_py
from rclpy.serialization import deserialize_message
from sensomative_raw_msgs.msg import RawNotification


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).expanduser().read_text(encoding='utf-8').splitlines()]


def rate(values):
    return (len(values)-1)*1e9/(values[-1]-values[0]) if len(values)>1 and values[-1]>values[0] else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--windows-log', required=True)
    p.add_argument('--receiver-log', required=True)
    p.add_argument('--transport-log', help='Optional replay sender audit; Windows original remains --windows-log')
    p.add_argument('--bag', required=True)
    p.add_argument('--topic', default='/sensomative/raw')
    p.add_argument('--report', required=True)
    args = p.parse_args()
    original_rows = read_jsonl(args.windows_log)
    originals = [r['frame'] for r in original_rows if r['kind']=='ble_notification']
    if not originals:
        raise SystemExit('No Windows original notifications')
    receiver_rows = read_jsonl(args.receiver_log)
    transport_rows = read_jsonl(args.transport_log) if args.transport_log else original_rows
    received = [r for r in receiver_rows if r['kind']=='received']
    published = [r for r in receiver_rows if r['kind']=='published']
    sent = {r['frame_id'] for r in transport_rows if r['kind']=='tcp_sent' and r['type']=='notification'}
    acked = {r['frame_id'] for r in transport_rows if r['kind']=='tcp_ack' and r['type']=='notification'}
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(Path(args.bag).expanduser()), storage_id='sqlite3'),
                rosbag2_py.ConverterOptions('', ''))
    topics = {t.name:t.type for t in reader.get_all_topics_and_types()}
    messages=[]
    while reader.has_next():
        topic, data, _ = reader.read_next()
        if topic==args.topic:
            messages.append(deserialize_message(data,RawNotification))
    errors=[]
    counts=dict(ble=len(originals),tcp_sent_unique=len(sent),tcp_acked_unique=len(acked),
                wsl_received=len(received),ros_published=len(published),bag=len(messages))
    if len(set(counts.values()))!=1:
        errors.append(dict(issue='counts_differ',counts=counts))
    fields={'protocol_version':'protocol_version','session_id':'session_id','frame_id':'transport_frame_id',
            'windows_sequence':'windows_sequence','windows_received_utc_ns':'windows_received_utc_ns',
            'windows_monotonic_ns':'windows_monotonic_ns','characteristic_uuid':'characteristic_uuid',
            'payload_length':'payload_length'}
    for i,(frame,msg) in enumerate(zip(originals,messages)):
        for key,attr in fields.items():
            if frame[key]!=getattr(msg,attr):
                errors.append(dict(index=i,sequence=frame['windows_sequence'],field=key))
        if frame['payload']!=list(msg.payload):
            errors.append(dict(index=i,sequence=frame['windows_sequence'],field='all_payload_bytes'))
        if msg.payload_length!=len(msg.payload):
            errors.append(dict(index=i,field='bag_payload_length'))
        if i<len(received):
            for key in ('wsl_received_utc_ns','wsl_monotonic_ns'):
                if getattr(msg,key)!=received[i][key]:
                    errors.append(dict(index=i,field=key))
        if i<len(published) and msg.ros_publish_time.sec*10**9+msg.ros_publish_time.nanosec!=published[i]['ros_publish_ns']:
            errors.append(dict(index=i,field='ros_publish_time'))
    keys=[(f['session_id'],f['windows_sequence']) for f in originals]
    if len(set(keys))!=len(keys):
        errors.append(dict(issue='duplicate_windows_sequence'))
    for a,b in zip(originals,originals[1:]):
        if a['session_id']==b['session_id'] and b['windows_sequence']!=a['windows_sequence']+1:
            errors.append(dict(issue='windows_sequence_gap_or_reorder',previous=a['windows_sequence'],current=b['windows_sequence']))
    for stage,entries in [('received',received),('published',published)]:
        if [(r['session_id'],r['sequence']) for r in entries]!=keys:
            errors.append(dict(issue=stage+'_sequence_or_order_mismatch'))
    if topics!={args.topic:'sensomative_raw_msgs/msg/RawNotification'}:
        errors.append(dict(issue='unexpected_bag_topics',topics=topics))
    diagnostics=[r for r in receiver_rows if r['kind'] in ('sequence_gap','sequence_duplicate','sequence_reversed','payload_length_changed','receiver_error')]
    if diagnostics:
        errors.append(dict(issue='receiver_diagnostics',events=diagnostics))
    frames={r['frame']['frame_id']:r['frame'] for r in original_rows if r['kind'] in ('event','ble_notification')}
    ack_frames=[frames[i] for i in acked if i in frames]
    if not acked.issubset(frames) or not sent.issubset(frames):
        errors.append(dict(issue='unknown_transport_frame_ids'))
    if {(f['session_id'],f['windows_sequence']) for f in ack_frames}!=set(keys):
        errors.append(dict(issue='tcp_ack_set_mismatch'))
    intervals=[(b['windows_monotonic_ns']-a['windows_monotonic_ns'])/1e9 for a,b in zip(originals,originals[1:])]
    report=dict(passed=not errors,counts=counts,all_fields_and_payloads_compared=True,
        sources=dict(windows_log=args.windows_log,receiver_log=args.receiver_log,
                     transport_log=args.transport_log or args.windows_log,bag=args.bag),
        lengths=dict(Counter(f['payload_length'] for f in originals)),
        ble_callback_rate_hz=rate([f['windows_monotonic_ns'] for f in originals]),
        tcp_receive_rate_hz=rate([r['wsl_monotonic_ns'] for r in received]),
        ros_publish_rate_hz=rate([r['publish_monotonic_ns'] for r in published]),
        duration_first_to_last_s=(originals[-1]['windows_monotonic_ns']-originals[0]['windows_monotonic_ns'])/1e9,
        callback_interval_s=dict(min=min(intervals),max=max(intervals),mean=statistics.mean(intervals),median=statistics.median(intervals)) if intervals else None,
        errors=errors)
    with Path(args.report).expanduser().open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2)
    print(json.dumps(report,indent=2))
    if errors:
        raise SystemExit(1)

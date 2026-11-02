import argparse
import hashlib
import json
from pathlib import Path
import socket
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from sensomative_raw_msgs.msg import RawNotification
from .protocol import VERSION, encode, read_socket, validate


class RawReceiver(Node):
    def __init__(self, args):
        super().__init__('sensomative_raw_bridge')
        self.args = args
        self.pub = self.create_publisher(RawNotification, args.topic, QoSProfile(
            depth=4096, reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE))
        self.stopping = threading.Event()
        self.sessions = {}
        self.received = 0
        self.published = 0
        self.logfile = Path(args.log).expanduser().open('x', encoding='utf-8')
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((args.host, args.port))
        self.listener.listen(1)
        self.listener.settimeout(.5)
        self.log('listening', host=args.host, port=self.listener.getsockname()[1], topic=args.topic)
        self.thread = threading.Thread(target=self.serve, daemon=False)
        self.thread.start()

    def log(self, kind, **fields):
        row = dict(kind=kind, wsl_log_utc_ns=time.time_ns(), **fields)
        self.logfile.write(json.dumps(row) + '\n')
        self.logfile.flush()
        if kind not in ('published', 'received'):
            self.get_logger().info(json.dumps(row))

    def process(self, frame, utc_ns, mono_ns):
        validate(frame)
        session = frame['session_id']
        state = self.sessions.setdefault(session, dict(frame=0, sequence=0, length=None, digest=None))
        fid = frame['frame_id']
        digest = hashlib.sha256(encode(frame)).hexdigest()
        if fid <= state['frame']:
            if fid != state['frame'] or digest != state['digest']:
                raise ValueError('Old or conflicting transport frame')
            self.log('transport_replay_suppressed', session_id=session, frame_id=fid)
            return
        if fid != state['frame']+1:
            raise ValueError('Transport frame gap: expected %d, got %d' % (state['frame']+1, fid))
        if frame['type'] == 'event':
            self.log('windows_event', session_id=session, frame_id=fid, event=frame['event'], details=frame.get('details'))
        else:
            seq = frame['windows_sequence']
            previous = state['sequence']
            if seq != previous+1:
                issue = 'sequence_duplicate' if seq == previous else 'sequence_reversed' if seq < previous else 'sequence_gap'
                self.log(issue, session_id=session, previous=previous, current=seq)
            if state['length'] is not None and frame['payload_length'] != state['length']:
                self.log('payload_length_changed', previous=state['length'], current=frame['payload_length'], sequence=seq)
            elif state['length'] is None and frame['payload_length'] != self.args.expected_length:
                self.log('unexpected_initial_length', expected=self.args.expected_length, current=frame['payload_length'])
            # A complete validated unique frame has arrived, before waiting for a subscriber.
            self.received += 1
            self.log('received', session_id=session, sequence=seq, received_count=self.received,
                     wsl_received_utc_ns=utc_ns, wsl_monotonic_ns=mono_ns)
            # Never publish unobserved startup samples when recording is required.
            if self.args.require_subscriber:
                while self.pub.get_subscription_count() == 0:
                    if self.stopping.wait(.1):
                        raise EOFError('Shutdown while awaiting ROS subscriber')
            msg = RawNotification()
            msg.protocol_version = VERSION
            msg.session_id = session
            msg.transport_frame_id = fid
            msg.windows_sequence = seq
            msg.windows_received_utc_ns = frame['windows_received_utc_ns']
            msg.windows_monotonic_ns = frame['windows_monotonic_ns']
            msg.wsl_received_utc_ns = utc_ns
            msg.wsl_monotonic_ns = mono_ns
            msg.characteristic_uuid = frame['characteristic_uuid']
            msg.payload_length = frame['payload_length']
            msg.payload = bytes(frame['payload'])
            msg.ros_publish_time = self.get_clock().now().to_msg()
            publish_mono_ns = time.monotonic_ns()
            self.pub.publish(msg)
            self.published += 1
            self.log('published', session_id=session, sequence=seq, frame_id=fid,
                     published_count=self.published, publish_monotonic_ns=publish_mono_ns,
                     ros_publish_ns=msg.ros_publish_time.sec*10**9+msg.ros_publish_time.nanosec)
            state.update(sequence=max(previous, seq), length=frame['payload_length'])
        state['frame'] = fid
        state['digest'] = digest

    def serve(self):
        while not self.stopping.is_set():
            try:
                conn, peer = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.log('tcp_connected', peer=str(peer))
            with conn:
                conn.settimeout(.5)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                try:
                    while not self.stopping.is_set():
                        frame = read_socket(conn, self.stopping)
                        utc_ns, mono_ns = time.time_ns(), time.monotonic_ns()
                        self.process(frame, utc_ns, mono_ns)
                        conn.sendall(encode(dict(type='ack', protocol_version=VERSION,
                                                 session_id=frame['session_id'], frame_id=frame['frame_id'])))
                except (EOFError, OSError, ValueError, KeyError, TypeError) as exc:
                    self.log('tcp_disconnected', reason=str(exc))
                except Exception as exc:
                    self.log('receiver_error', reason=repr(exc))
        self.log('receiver_summary', received=self.received, published=self.published)

    def close(self):
        self.stopping.set()
        self.listener.close()
        self.thread.join()
        self.logfile.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--topic', default='/sensomative/raw')
    parser.add_argument('--expected-length', type=int, default=13)
    parser.add_argument('--require-subscriber', action='store_true')
    parser.add_argument('--log', required=True, help='New JSONL audit file; existing files are never overwritten')
    args, ros_args = parser.parse_known_args()
    rclpy.init(args=ros_args)
    node = RawReceiver(args)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

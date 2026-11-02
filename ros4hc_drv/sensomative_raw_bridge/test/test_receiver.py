"""ROS integration: real socket framing, ACK loss retry, anomaly diagnostics."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

import rclpy
from sensomative_raw_bridge.protocol import encode, read_socket
from sensomative_raw_bridge.receiver import RawReceiver


class ReceiverIntegration(unittest.TestCase):
    def test_ack_loss_reconnect_and_diagnostics(self):
        rclpy.init()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'receiver.jsonl'
            args=SimpleNamespace(topic='/sensomative/test_raw',host='127.0.0.1',port=0,
                                 log=str(path),require_subscriber=False,expected_length=13)
            node=RawReceiver(args)
            port=node.listener.getsockname()[1]
            frame=dict(protocol_version=1,type='notification',
                       session_id='12345678-1234-1234-1234-123456789abc',frame_id=1,
                       windows_sequence=1,windows_received_utc_ns=1700000000000000000,
                       windows_monotonic_ns=1000,
                       characteristic_uuid='000055c2-0000-1000-8000-00805f9b34fb',
                       payload_length=13,payload=list(range(13)))
            try:
                # Publish first frame but deliberately do not receive its ACK.
                sock=socket.create_connection(('127.0.0.1',port))
                sock.sendall(encode(frame))
                deadline=time.monotonic()+5
                while node.published!=1 and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertEqual(node.published,1)
                sock.close()
                with socket.create_connection(('127.0.0.1',port)) as sock:
                    sock.settimeout(2)
                    sock.sendall(encode(frame))
                    self.assertEqual(read_socket(sock,threading.Event())['frame_id'],1)
                    self.assertEqual(node.published,1)
                    for fid,seq,n in ((2,3,14),(3,3,14),(4,2,14)):
                        item=dict(frame,frame_id=fid,windows_sequence=seq,payload_length=n,payload=list(range(n)))
                        sock.sendall(encode(item))
                        self.assertEqual(read_socket(sock,threading.Event())['frame_id'],fid)
                self.assertEqual(node.published,4)
                with socket.create_connection(('127.0.0.1',port)) as sock:
                    sock.sendall(encode(dict(frame,frame_id=6)))
                    sock.settimeout(2)
                    self.assertEqual(sock.recv(1),b'')
            finally:
                node.close()
                node.destroy_node()
                rclpy.shutdown()
            kinds=[json.loads(line)['kind'] for line in path.read_text().splitlines()]
            for kind in ('transport_replay_suppressed','sequence_gap','sequence_duplicate',
                         'sequence_reversed','payload_length_changed','tcp_disconnected'):
                self.assertIn(kind,kinds)


if __name__=='__main__':
    unittest.main()

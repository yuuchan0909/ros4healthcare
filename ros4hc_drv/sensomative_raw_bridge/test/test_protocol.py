import asyncio
import socket
import struct
import threading
import unittest

from sensomative_raw_bridge.protocol import MAX_FRAME, encode, read_async, read_socket, validate


def notification(**overrides):
    frame = dict(protocol_version=1, type='notification',
                 session_id='12345678-1234-1234-1234-123456789abc', frame_id=1,
                 windows_sequence=1, windows_received_utc_ns=1700000000000000123,
                 windows_monotonic_ns=876543210012345,
                 characteristic_uuid='000055c2-0000-1000-8000-00805f9b34fb',
                 payload_length=13, payload=[0,1,2,3,4,5,6,7,8,9,10,255,42])
    frame.update(overrides)
    return frame


class FramingTest(unittest.TestCase):
    def test_fragmented_and_coalesced_stream(self):
        a,b=socket.socketpair()
        frames=[notification(),notification(frame_id=2,windows_sequence=2,payload_length=17,payload=list(range(17)))]
        wire=b''.join(encode(f) for f in frames)
        def send():
            with a:
                for i in range(0,len(wire),3):
                    a.sendall(wire[i:i+3])
        thread=threading.Thread(target=send)
        thread.start()
        with b:
            stop=threading.Event()
            self.assertEqual(read_socket(b,stop),frames[0])
            self.assertEqual(read_socket(b,stop),frames[1])
            with self.assertRaises(EOFError):
                read_socket(b,stop)
        thread.join()

    def test_async_stream_and_truncated_frame(self):
        async def run():
            reader=asyncio.StreamReader()
            wire=encode(notification())
            reader.feed_data(wire[:2]); reader.feed_data(wire[2:]); reader.feed_eof()
            self.assertEqual(await read_async(reader),notification())
            reader=asyncio.StreamReader()
            reader.feed_data(wire[:-1]); reader.feed_eof()
            with self.assertRaises(asyncio.IncompleteReadError):
                await read_async(reader)
            reader=asyncio.StreamReader()
            reader.feed_data(struct.pack('!I',MAX_FRAME+1))
            with self.assertRaises(ValueError):
                await read_async(reader)
        asyncio.run(run())

    def test_payload_validation_preserves_variable_lengths(self):
        for n in (0,13,20,255):
            validate(notification(payload_length=n,payload=[255]*n))
        for overrides in (dict(payload_length=12),dict(payload=[0]*12+[256]),
                          dict(payload=[0]*12+[True]),dict(protocol_version=2),
                          dict(windows_sequence=-1),dict(windows_received_utc_ns=2**63)):
            with self.assertRaises(ValueError):
                validate(notification(**overrides))


if __name__=='__main__':
    unittest.main()

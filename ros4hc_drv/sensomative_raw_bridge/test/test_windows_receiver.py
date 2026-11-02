"""Exercise Windows-independent sender paths without accessing Bluetooth."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from sensomative_raw_bridge.protocol import encode, read_async

spec=importlib.util.spec_from_file_location('windows_receiver',Path(__file__).resolve().parents[1]/'windows/sensomative_ble_receiver.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WindowsSenderTest(unittest.TestCase):
    def test_event_device_name_and_callback_bytes_with_ack_retry(self):
        async def run(tmp):
            received=[]
            attempts=0
            async def peer(reader,writer):
                nonlocal attempts
                try:
                    while True:
                        frame=await read_async(reader)
                        received.append(frame)
                        attempts+=1
                        # Lose the first ACK to force the original frame to be retried.
                        if attempts==1:
                            break
                        writer.write(encode(dict(type='ack',protocol_version=1,
                                                 session_id=frame['session_id'],frame_id=frame['frame_id'])))
                        await writer.drain()
                except asyncio.IncompleteReadError:
                    pass
                finally:
                    writer.close()
                    await writer.wait_closed()
            server=await asyncio.start_server(peer,'127.0.0.1',0)
            args=SimpleNamespace(log=str(Path(tmp)/'windows.jsonl'),host='127.0.0.1',
                                 port=server.sockets[0].getsockname()[1],ack_timeout=2,seconds=30)
            receiver=module.Receiver(args)
            receiver.event('ble_connected',name='Sensomative_8370')
            import time
            receiver.start_ns=time.perf_counter_ns()
            payload=bytearray([0,255,1,2,3,4,5,6,7,8,9,10,11])
            receiver.notification(SimpleNamespace(uuid='000055c2-0000-1000-8000-00805f9b34fb'),payload)
            payload[0]=99
            receiver.finished.set()
            receiver.changed.set()
            await asyncio.wait_for(receiver.sender(),8)
            receiver.audit.close()
            server.close()
            await server.wait_closed()
            self.assertEqual(receiver.sequence,1)
            self.assertEqual(receiver.sent,1)
            self.assertEqual(receiver.acked,1)
            self.assertEqual(len(receiver.pending),0)
            self.assertEqual(received[0],received[1])
            self.assertEqual(received[-1]['payload'],[0,255,1,2,3,4,5,6,7,8,9,10,11])
            rows=[json.loads(line) for line in Path(args.log).read_text().splitlines()]
            self.assertEqual(sum(r['kind']=='ble_notification' for r in rows),1)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(tmp))


if __name__=='__main__':
    unittest.main()

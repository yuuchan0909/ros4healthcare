"""Run with Windows Python, never WSL Python. All BLE bytes are preserved."""
import argparse
import asyncio
from collections import deque
import json
import os
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sensomative_raw_bridge.protocol import VERSION, encode, read_async, validate


class Receiver:
    def __init__(self, args):
        self.args = args
        self.session = str(uuid.uuid4())
        self.frames = 0
        self.sequence = 0
        self.sent = 0
        self.attempts = 0
        self.pending = deque()
        self.changed = asyncio.Event()
        self.finished = asyncio.Event()
        self.stop = asyncio.Event()
        self.start_ns = None
        self.audit = Path(args.log).open('x', encoding='utf-8')
        self.acked = 0
        self.highest_sent_id = 0
        self.ui = None

    def log(self, kind, **fields):
        row = dict(kind=kind, log_utc_ns=time.time_ns(), **fields)
        self.audit.write(json.dumps(row) + '\n')
        self.audit.flush()
        if kind not in ('ble_notification', 'tcp_sent', 'tcp_ack'):
            print(json.dumps(row), flush=True)

    def enqueue(self, kind, **fields):
        self.frames += 1
        frame = dict(protocol_version=VERSION, type=kind, session_id=self.session,
                     frame_id=self.frames, **fields)
        validate(frame)
        # Persist the original before making it eligible for TCP transmission.
        self.log('ble_notification' if kind == 'notification' else 'event', frame=frame)
        self.pending.append(frame)
        self.changed.set()

    def event(self, event_name, **details):
        self.enqueue('event', event=event_name, details=details)

    async def sender(self):
        while not self.finished.is_set() or self.pending:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(self.args.host, self.args.port), 5)
                sock = writer.get_extra_info('socket')
                import socket
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.log('tcp_connected', host=self.args.host, port=self.args.port)
                while not self.finished.is_set() or self.pending:
                    if not self.pending:
                        self.changed.clear()
                        await self.changed.wait()
                        continue
                    frame = self.pending[0]
                    writer.write(encode(frame))
                    await writer.drain()
                    self.attempts += 1
                    if frame['type'] == 'notification' and frame['frame_id'] > self.highest_sent_id:
                        self.sent += 1
                    self.highest_sent_id = max(self.highest_sent_id, frame['frame_id'])
                    self.log('tcp_sent', frame_id=frame['frame_id'], type=frame['type'])
                    ack = await asyncio.wait_for(read_async(reader), self.args.ack_timeout)
                    if (ack.get('type'), ack.get('protocol_version'), ack.get('session_id'), ack.get('frame_id')) != ('ack', VERSION, self.session, frame['frame_id']):
                        raise ValueError('Invalid ACK')
                    self.pending.popleft()
                    if frame['type'] == 'notification':
                        self.acked += 1
                    self.log('tcp_ack', frame_id=frame['frame_id'], type=frame['type'])
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
                self.log('tcp_disconnected', error=repr(exc), pending=len(self.pending))
                await asyncio.sleep(1)
            finally:
                if writer:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass

    def notification(self, sender, data):
        utc_ns, mono_ns = time.time_ns(), time.perf_counter_ns()
        if self.start_ns is None or mono_ns < self.start_ns:
            return
        elapsed = (mono_ns-self.start_ns)/1e9
        if self.args.seconds and elapsed >= self.args.seconds:
            return
        self.sequence += 1
        self.enqueue('notification', windows_sequence=self.sequence,
                     windows_received_utc_ns=utc_ns, windows_monotonic_ns=mono_ns,
                     characteristic_uuid=str(uuid.UUID(sender.uuid)),
                     payload_length=len(data), payload=list(data))

    async def capture(self):
        from bleak import BleakClient, BleakScanner
        while not self.stop.is_set():
            try:
                self.event('ble_connecting', address=self.args.address)
                device = await BleakScanner.find_device_by_address(self.args.address, timeout=15)
                if device is None:
                    raise RuntimeError('BLE device not found')
                def disconnected(_client):
                    self.event('ble_disconnected', address=self.args.address)
                async with BleakClient(device, timeout=25, disconnected_callback=disconnected,
                                       winrt={'use_cached_services': False}) as client:
                    char = client.services.get_characteristic(self.args.uuid)
                    if char is None or 'notify' not in char.properties:
                        raise RuntimeError('Requested notify characteristic not present')
                    self.event('ble_connected', address=device.address, name=device.name, properties=char.properties)
                    await client.start_notify(char, self.notification)
                    if self.start_ns is None:
                        if self.args.guided:
                            self.ui.ready = True
                            self.ui.status = 'BLE接続済み。「開始」を押すと5秒後に記録開始'
                            while self.ui.start_ns is None and not self.ui.cancel:
                                await asyncio.sleep(.05)
                            if self.ui.cancel:
                                return
                            self.start_ns = self.ui.start_ns
                        else:
                            self.start_ns = time.perf_counter_ns()
                        self.event('capture_started', start_monotonic_ns=self.start_ns, duration_s=self.args.seconds)
                    while client.is_connected and not self.stop.is_set():
                        if self.args.seconds and time.perf_counter_ns() >= self.start_ns+int(self.args.seconds*1e9):
                            self.stop.set()
                            break
                        await asyncio.sleep(.02)
                    if client.is_connected:
                        await client.stop_notify(char)
            except Exception as exc:
                self.event('ble_error', error=repr(exc))
                if self.ui:
                    self.ui.status = 'BLE再接続中：' + str(exc)
                await asyncio.sleep(2)
            if self.start_ns and self.args.seconds and time.perf_counter_ns() >= self.start_ns+int(self.args.seconds*1e9):
                self.stop.set()

    async def run(self):
        sender = asyncio.create_task(self.sender())
        self.event('session_started', python=sys.version, platform=sys.platform)
        async def heartbeat():
            while True:
                await asyncio.sleep(2)
                self.event('heartbeat', ble_notifications=self.sequence)
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            await self.capture()
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            self.event('capture_finished', notifications=self.sequence)
            self.finished.set()
            self.changed.set()
            try:
                await asyncio.wait_for(sender, self.args.drain_timeout)
            except asyncio.TimeoutError:
                self.log('drain_timeout', pending=len(self.pending))
            self.log('summary', session_id=self.session, ble_notifications=self.sequence,
                     tcp_sent_unique_notifications=self.sent, tcp_acked_notifications=self.acked,
                     transport_send_attempts=self.attempts, pending_frames=len(self.pending))
            self.audit.flush()
            os.fsync(self.audit.fileno())
            self.audit.close()
            if self.ui:
                self.ui.done = True
                self.ui.status = f'完了：BLE {self.sequence} / ACK {self.acked} / 未送達frame {len(self.pending)}'

    async def replay(self, path):
        originals = [json.loads(line)['frame'] for line in Path(path).read_text(encoding='utf-8').splitlines()
                     if json.loads(line)['kind'] in ('ble_notification', 'event')]
        if not originals:
            raise ValueError('No original frames in replay log')
        self.session = originals[0]['session_id']
        for frame in originals:
            validate(frame)
            if frame['session_id'] != self.session:
                raise ValueError('Multiple sessions in replay log')
            self.pending.append(frame)
            if frame['type'] == 'notification':
                self.sequence += 1
        self.finished.set()
        self.changed.set()
        try:
            await asyncio.wait_for(self.sender(), self.args.drain_timeout)
        finally:
            self.log('replay_summary', session_id=self.session, notifications=self.sequence,
                     sent=self.sent, acked=self.acked, pending=len(self.pending), source=path)
            self.audit.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--address', help='BLE address; required unless --replay-log is used')
    p.add_argument('--uuid', default='000055c2-0000-1000-8000-00805f9b34fb')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--log', required=True, help='New Windows original JSONL audit file')
    p.add_argument('--deps', help='Optional isolated Windows dependency directory')
    p.add_argument('--seconds', type=float, default=0, help='0: until Ctrl+C; otherwise wall time after start')
    p.add_argument('--ack-timeout', type=float, default=15)
    p.add_argument('--drain-timeout', type=float, default=60)
    p.add_argument('--guided', action='store_true', help='Windows GUI, 30s load trial with a local Start button')
    p.add_argument('--replay-log', help='Replay ALL original frames to a fresh WSL receiver/bag; no BLE access')
    args = p.parse_args()
    if not args.replay_log and not (args.address and args.address.strip()):
        p.error('--address is required for BLE capture')
    if sys.platform != 'win32':
        p.error('Use Windows Python, not WSL Python')
    if args.guided and args.seconds != 30:
        p.error('--guided requires --seconds 30')
    if args.seconds < 0 or not 0 < args.port < 65536:
        p.error('Invalid seconds or port')
    if args.deps:
        sys.path.insert(0, args.deps)
    if args.replay_log:
        asyncio.run(Receiver(args).replay(args.replay_log))
    elif args.guided:
        from guided_ui import run_guided
        run_guided(args, Receiver)
    else:
        try:
            asyncio.run(Receiver(args).run())
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()

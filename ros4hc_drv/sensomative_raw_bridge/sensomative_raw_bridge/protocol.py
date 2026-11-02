"""Protocol v1: 4-byte unsigned big-endian length + UTF-8 JSON object."""
import json
import struct
import uuid

VERSION = 1
MAX_FRAME = 1024 * 1024
MAX_PAYLOAD = 65535


def encode(frame):
    data = json.dumps(frame, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if not 0 < len(data) <= MAX_FRAME:
        raise ValueError('Invalid frame size')
    return struct.pack('!I', len(data)) + data


def decode(data):
    frame = json.loads(data)
    if not isinstance(frame, dict):
        raise ValueError('Expected JSON object')
    return frame


def integer(frame, name, minimum=0, maximum=2**64-1):
    value = frame[name]
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid ' + name)
    return value


def validate(frame):
    if integer(frame, 'protocol_version', 1, 65535) != VERSION:
        raise ValueError('Unsupported protocol version')
    uuid.UUID(frame['session_id'])
    integer(frame, 'frame_id', 1)
    if frame['type'] == 'notification':
        integer(frame, 'windows_sequence', 1)
        integer(frame, 'windows_received_utc_ns', 0, 2**63-1)
        integer(frame, 'windows_monotonic_ns')
        if str(uuid.UUID(frame['characteristic_uuid'])) != frame['characteristic_uuid']:
            raise ValueError('UUID must be canonical lowercase')
        n = integer(frame, 'payload_length', 0, MAX_PAYLOAD)
        values = frame['payload']
        if not isinstance(values, list) or len(values) != n:
            raise ValueError('Payload length mismatch')
        if any(type(v) is not int or not 0 <= v <= 255 for v in values):
            raise ValueError('Invalid raw byte')
    elif frame['type'] == 'event':
        if not isinstance(frame['event'], str):
            raise ValueError('Invalid event')
    else:
        raise ValueError('Unknown frame type')


async def read_async(reader):
    prefix = await reader.readexactly(4)
    n = struct.unpack('!I', prefix)[0]
    if not 0 < n <= MAX_FRAME:
        raise ValueError('Invalid frame size')
    return decode(await reader.readexactly(n))


def read_socket(sock, stopping):
    import socket
    def exact(n):
        parts = bytearray()
        while len(parts) < n:
            if stopping.is_set():
                raise EOFError('Stopping')
            try:
                data = sock.recv(n-len(parts))
            except socket.timeout:
                continue
            if not data:
                raise EOFError('TCP peer closed')
            parts.extend(data)
        return bytes(parts)
    n = struct.unpack('!I', exact(4))[0]
    if not 0 < n <= MAX_FRAME:
        raise ValueError('Invalid frame size')
    return decode(exact(n))

"""KSLT1 transport-test packets. Runs unchanged on CPython/MicroPython."""
try:
    import ustruct as struct
except ImportError:
    import struct
try:
    import ubinascii as binascii
except ImportError:
    import binascii

INPUT = 1
ACK = 2
STOP = 3
PREFIX = b'KSLT1:'
FMT = '<BIIB'

def crc32(data):
    native = getattr(binascii, 'crc32', None)
    if native is not None:
        return native(data) & 0xffffffff
    crc = 0xffffffff
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xedb88320 if crc & 1 else 0)
    return crc ^ 0xffffffff

def validate(kind, session, frame, mask):
    if (kind not in (INPUT, ACK, STOP) or not 1 <= session <= 0xffffffff
            or not 0 <= frame <= 0xffffffff or not 0 <= mask <= 63
            or (kind != INPUT and mask != 0)
            or (kind == INPUT and frame == 0xffffffff)
            or (kind == STOP and frame != 0xffffffff)):
        raise ValueError('invalid packet fields')

def encode(kind, session, frame, mask=0):
    validate(kind, session, frame, mask)
    body = struct.pack(FMT, kind, session, frame, mask)
    return PREFIX + binascii.hexlify(body + struct.pack('<I', crc32(body))) + b'\n'

def decode(line):
    if not line.startswith(PREFIX) or not line.endswith(b'\n'):
        raise ValueError('framing')
    text = line[len(PREFIX):-1]
    if text.endswith(b'\r'):
        text = text[:-1]
    if len(text) != 28 or any(c not in b'0123456789abcdefABCDEF' for c in text):
        raise ValueError('hex/length')
    raw = binascii.unhexlify(text)
    body = raw[:-4]
    if crc32(body) != struct.unpack('<I', raw[-4:])[0]:
        raise ValueError('CRC')
    packet = struct.unpack(FMT, body)
    validate(*packet)
    return packet

class Parser:
    def __init__(self):
        self.buffer = bytearray()
        self.dropping = False
        self.errors = 0

    def feed(self, data):
        packets = []
        for value in data:
            if value == 10:
                if not self.dropping:
                    try:
                        packets.append(decode(bytes(self.buffer) + b'\n'))
                    except ValueError:
                        self.errors += 1
                self.buffer = bytearray()
                self.dropping = False
            elif not self.dropping:
                if len(self.buffer) >= 64:
                    self.buffer = bytearray()
                    self.dropping = True
                    self.errors += 1
                else:
                    self.buffer.append(value)
        return packets

STOP_FRAME = 0xffffffff

class Endpoint:
    def __init__(self, session):
        validate(INPUT, session, 0, 0)
        self.session = session
        self.next_frame = 0
        self.pending = {}
        self.history = {}
        self.remote_mask = 0
        self.stopped = False
        self.stop_acked = False
        self.highest = -1
        self.rejected = 0

    def send(self, mask, now):
        if self.stopped or STOP_FRAME in self.pending or len(self.pending) >= 32:
            raise ValueError('send window full or stopping')
        frame = self.next_frame
        line = encode(INPUT, self.session, frame, mask)
        self.pending[frame] = [line, now, 1]
        self.next_frame += 1
        return line

    def stop(self, now):
        line = encode(STOP, self.session, STOP_FRAME)
        self.pending = {STOP_FRAME: [line, now, 1]}
        return line

    def due(self, now):
        lines = []
        for item in self.pending.values():
            if now - item[1] >= 100:
                if item[2] >= 10:
                    raise RuntimeError('ACK timeout')
                item[1] = now
                item[2] += 1
                lines.append(item[0])
        return lines

    def handle(self, packet):
        kind, session, frame, mask = packet
        validate(*packet)
        if session != self.session:
            self.rejected += 1
            return []
        if kind == ACK:
            existed = frame in self.pending
            self.pending.pop(frame, None)
            if frame == STOP_FRAME and existed:
                self.stop_acked = True
            return []
        if kind == STOP:
            self.stopped = True
            self.remote_mask = 0
            self.pending = {}
        else:
            if self.stopped or frame <= self.highest - 32:
                self.rejected += 1
                return []
            if frame in self.history and self.history[frame] != mask:
                raise ValueError('conflicting duplicate frame')
            self.history[frame] = mask
            if frame > self.highest:
                self.highest = frame
                self.remote_mask = mask
            for old in list(self.history):
                if old <= self.highest - 32:
                    del self.history[old]
        return [encode(ACK, self.session, frame)]

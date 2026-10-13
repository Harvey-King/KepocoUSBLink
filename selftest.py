"""Real localhost TCP link running the actual PC and device controllers.
Fake Kepoco buttons/USB streams ONLY: no physical serial ports are opened.
"""
import json
import select
import socket
import threading
import time
from types import SimpleNamespace
import protocol as p
import device_link
import pc_link

class SocketSerial:
    def __init__(self, sock, drop=None):
        self.sock, self.drop = sock, drop
        sock.settimeout(.01)
    @property
    def in_waiting(self):
        if select.select([self.sock], [], [], 0)[0]:
            return len(self.sock.recv(4096, socket.MSG_PEEK))
        return 0
    def read(self, count=1):
        try:
            return self.sock.recv(count)
        except socket.timeout:
            return b''
    def write(self, data):
        if not self.drop or not self.drop(data):
            self.sock.sendall(data)
        return len(data)

class InputStream:
    def __init__(self, sock): self.sock = sock
    def read(self, count): return self.sock.recv(count).decode('ascii')

class OutputStream:
    def __init__(self, sock, drop): self.sock, self.drop = sock, drop
    def write(self, text):
        data = text.encode('ascii')
        if not self.drop(data):
            self.sock.sendall(data)
        return len(text)
    def flush(self): pass

class Poller:
    def __init__(self, stream): self.stream = stream
    def poll(self, delay):
        ready = select.select([self.stream.sock], [], [], delay / 1000)[0]
        return [(self.stream, 1, 'host-extra-field')] if ready else []

class Button:
    def __init__(self, held): self.held = held
    def pressed(self): return self.held

def run_selftest(capture='selftest-capture.jsonl'):
    dropped = {'pc_input': 0, 'device_ack': 0}
    def lose_first_input(data):
        if p.decode(data)[0] == p.INPUT and not dropped['pc_input']:
            dropped['pc_input'] += 1
            return True
        return False
    def lose_first_ack(data):
        if p.decode(data)[0] == p.ACK and not dropped['device_ack']:
            dropped['device_ack'] += 1
            return True
        return False
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname())
    server, _ = listener.accept()
    listener.close()
    server.settimeout(2)
    stream = InputStream(server)
    buttons = SimpleNamespace(**{name: Button(bit in (0, 4)) for bit, name in enumerate(
        ('buttonU','buttonD','buttonL','buttonR','buttonA','buttonB'))})
    device_result, masks, errors = {}, [], []
    def device():
        start = time.monotonic()
        try:
            device_result.update(device_link.run(12345, 3000, buttons, stream,
                OutputStream(server, lose_first_ack), Poller(stream),
                lambda: int((time.monotonic() - start) * 1000),
                lambda ms: time.sleep(ms / 1000), masks.append))
        except BaseException as ex:
            errors.append(repr(ex))
    worker = threading.Thread(target=device, daemon=True)
    worker.start()
    try:
        pc_result = pc_link.run_controller(SocketSerial(client, lose_first_input), 12345,
            pc_link.script_source('0,32,16,48,0', .08), .45, capture, .02)
        worker.join(2)
        if worker.is_alive():
            errors.append('device thread did not finish')
    finally:
        client.close()
        server.close()
        worker.join(2)
    passed = (not errors and pc_result['stop_acked'] and pc_result['last_received_mask'] == 17
              and device_result.get('reason') == 'peer-stop' and 32 in masks
              and dropped == {'pc_input': 1, 'device_ack': 1})
    return {'passed': passed, 'transport': 'real TCP localhost; fake Kepoco/USB streams',
            'hardware_verified': False, 'pc': pc_result, 'device': device_result,
            'device_masks': masks, 'dropped': dropped, 'errors': errors}

if __name__ == '__main__':
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', default='selftest-capture.jsonl')
    parser.add_argument('--report', default='selftest-report.json')
    args = parser.parse_args()
    result = run_selftest(args.capture)
    Path(args.report).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)

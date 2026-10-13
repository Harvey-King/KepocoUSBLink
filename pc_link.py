"""PC sender/catcher; pyserial transport supplied by CLI or localhost selftest."""
import argparse
import os
from pathlib import Path

def keyboard_mask(get_state=None):
    if get_state is None:
        if os.name != 'nt':
            raise RuntimeError('keyboard mode requires Windows; use --script elsewhere')
        import ctypes
        get_state = ctypes.windll.user32.GetAsyncKeyState
    if get_state(27) & 0x8000:
        return None
    keys = ((ord('W'), 38), (ord('S'), 40), (ord('A'), 37),
            (ord('D'), 39), (ord('J'),), (ord('K'),))
    return sum(1 << bit for bit, group in enumerate(keys)
               if any(get_state(key) & 0x8000 for key in group))

def script_source(text, interval):
    try:
        masks = [int(value.strip(), 0) for value in text.split(',')]
    except ValueError:
        raise ValueError('script must be comma-separated integer masks')
    if interval <= 0 or any(not 0 <= mask <= 63 for mask in masks):
        raise ValueError('invalid interval or mask (0..63)')
    return lambda elapsed: masks[min(int(elapsed / interval), len(masks) - 1)]

def argument_parser():
    parser = argparse.ArgumentParser(description='Silent bidirectional USB input link; no game code')
    parser.add_argument('--list', action='store_true', help='enumerate only; never open ports')
    parser.add_argument('--port', help='explicit confirmed Kepoco port')
    parser.add_argument('--launch', action='store_true', help='execute in RAM, retain same serial owner')
    parser.add_argument('--session', type=lambda value: int(value, 0), help='nonzero uint32; required without --launch')
    parser.add_argument('--script', help='finite comma-separated masks (0..63), one per interval')
    parser.add_argument('--interval', type=float, default=.033, help='sample/step interval in seconds')
    parser.add_argument('--seconds', type=float, help='duration; keyboard default 60; script default steps*interval')
    parser.add_argument('--capture', default='capture.jsonl', help='timestamped JSONL (overwritten)')
    return parser

def launch_device(port, session, max_ms, transport_class=None):
    """Installed mpremote exposes its pyboard-derived API as SerialTransport.
    exec_raw_no_follow returns while run() executes; .serial is the SAME handle.
    No mount, filesystem upload, follow(), soft reset or background mpremote.
    """
    if transport_class is None:
        from mpremote.transport_serial import SerialTransport
        transport_class = SerialTransport
    source = (Path(__file__).with_name('protocol.py').read_text(encoding='utf-8') + '\n' +
              Path(__file__).with_name('device_link.py').read_text(encoding='utf-8') +
              '\nrun(%d, %d)\n' % (session, max_ms))
    owner = transport_class(port, baudrate=115200, timeout=2)
    try:
        owner.enter_raw_repl(soft_reset=False, timeout_overall=5)
        owner.exec_raw_no_follow(source)
        owner.serial.timeout = .01
        owner.serial.write_timeout = 2
        return owner
    except BaseException:
        try:
            owner.exit_raw_repl()
        finally:
            owner.close()
        raise

import json
import time
from datetime import datetime, timezone
from protocol import Endpoint, Parser, decode, INPUT

def run_controller(serial, session, source, seconds, capture, interval=.033, on_mask=None):
    if seconds <= 0 or interval <= 0:
        raise ValueError('duration and interval must be positive')
    peer, parser = Endpoint(session), Parser()
    start = time.monotonic()
    next_sample = 0
    sent = 0
    last_received = 0
    reason = 'duration'
    log = open(capture, 'w', encoding='utf-8') if capture else None
    def record(direction, packet):
        if log:
            kind, sid, frame, mask = packet
            log.write(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                'elapsed_ms': int((time.monotonic() - start) * 1000),
                'direction': direction, 'kind': kind, 'session': sid,
                'frame': frame, 'mask': mask}) + '\n')
            log.flush()
    def write(line):
        if serial.write(line) != len(line):
            raise OSError('short transport write')
        record('tx', decode(line))
    def pump():
        nonlocal last_received
        data = serial.read(min(4096, max(1, serial.in_waiting)))
        for packet in parser.feed(data):
            record('rx', packet)
            previous = peer.highest
            for line in peer.handle(packet):
                write(line)
            if packet[0] == INPUT and peer.highest > previous:
                last_received = peer.remote_mask
                if on_mask:
                    on_mask(last_received)
    try:
        try:
            while time.monotonic() - start < seconds and not peer.stopped:
                pump()
                if peer.stopped:
                    break
                elapsed = time.monotonic() - start
                now = int(elapsed * 1000)
                if elapsed >= next_sample and len(peer.pending) < 32:
                    mask = source(elapsed)
                    if mask is None:
                        reason = 'escape'
                        break
                    write(peer.send(mask, now))
                    sent += 1
                    next_sample = elapsed + interval
                for line in peer.due(now):
                    write(line)
                time.sleep(.001)
            if peer.stopped:
                reason = 'peer-stop'
        except KeyboardInterrupt:
            reason = 'interrupt'
        finally:
            # STOP supersedes outstanding input samples and releases held masks.
            if not peer.stopped:
                write(peer.stop(int((time.monotonic() - start) * 1000)))
                deadline = time.monotonic() + 1.1
                while not peer.stop_acked and not peer.stopped and time.monotonic() < deadline:
                    pump()
                    for line in peer.due(int((time.monotonic() - start) * 1000)):
                        write(line)
                    time.sleep(.002)
    finally:
        peer.remote_mask = 0
        if log:
            log.close()
    return {'reason': reason, 'sent': sent, 'received_frame': peer.highest,
            'last_received_mask': last_received, 'stop_acked': peer.stop_acked,
            'parser_errors': parser.errors, 'rejected': peer.rejected}

def main(argv=None):
    import math
    import secrets
    import sys
    import serial
    from serial.tools import list_ports
    parser = argument_parser()
    args = parser.parse_args(argv)
    if args.list:
        for port in list_ports.comports():
            print('%s | %s | VID=%s PID=%s' % (port.device, port.description,
                  '%04x' % port.vid if port.vid is not None else 'none',
                  '%04x' % port.pid if port.pid is not None else 'none'))
        return 0
    if not args.port:
        parser.error('--port is required (first use --list and confirm the Kepoco)')
    if not args.launch and args.session is None:
        parser.error('--session is required without --launch; device must already run this session')
    session = args.session if args.session is not None else secrets.randbelow(0xffffffff) + 1
    if not 1 <= session <= 0xffffffff:
        parser.error('--session must be a nonzero uint32')
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error('--interval must be finite and positive')
    seconds = args.seconds
    if seconds is None:
        seconds = len(args.script.split(',')) * args.interval if args.script is not None else 60
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        parser.error('--seconds must be finite and within (0,3600]')
    try:
        source = script_source(args.script, args.interval) if args.script is not None else lambda elapsed: keyboard_mask()
    except ValueError as ex:
        parser.error(str(ex))
    if args.script is None and os.name != 'nt':
        parser.error('keyboard requires Windows; use --script')
    # Do not automatically open any enumerated device or infer Kepoco from VID.
    owner = None
    connection = None
    try:
        if args.launch:
            owner = launch_device(args.port, session, int(seconds * 1000) + 3000)
            connection = owner.serial
        else:
            connection = serial.serial_for_url(args.port, baudrate=115200, timeout=.01, write_timeout=2)
        print('Session %d; WASD/arrows, J=A K=B; Esc quits. Capture: %s' % (session, args.capture))
        result = run_controller(connection, session, source, seconds, args.capture, args.interval,
                                lambda mask: print('Kepoco held mask: %02x' % mask))
        print(json.dumps(result, indent=2))
        return 0 if result['stop_acked'] or result['reason'] == 'peer-stop' else 1
    except (OSError, ValueError, RuntimeError) as ex:
        print('error: %s' % ex, file=sys.stderr)
        return 1
    finally:
        if owner is not None:
            try:
                # Let the device's bounded STOP ACK linger finish, then restore
                # friendly REPL without rebooting or executing startup scripts.
                time.sleep(1.2)
                owner.serial.timeout = 1
                owner.enter_raw_repl(soft_reset=False, timeout_overall=3)
                owner.exit_raw_repl()
            except Exception as ex:
                print('REPL cleanup warning: %s' % ex, file=sys.stderr)
            finally:
                owner.close()
        elif connection is not None:
            connection.close()

if __name__ == '__main__':
    raise SystemExit(main())

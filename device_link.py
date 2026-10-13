"""Silent, temporary Kepoco button/link test; never changes startup or files.
Prepend protocol.py for single RAM execution; call run(session, max_ms).
"""
try:
    Endpoint
except NameError:
    from protocol import Endpoint, Parser, INPUT, STOP_FRAME

class LinkScreen:
    """72x40 live monitor: independent local and PC held-button rows."""
    def __init__(self, display):
        self.display = display

    def draw(self, local, remote, received, sent, status):
        display = self.display
        display.fill(0)
        display.drawText(b'USB INPUT', 0, 0, 1)
        for label, y, mask in ((b'KP', 10, local), (b'PC', 20, remote)):
            display.drawText(label, 0, y + 1, 1)
            for bit, letter in enumerate((b'U', b'D', b'L', b'R', b'A', b'B')):
                x = 18 + bit * 9
                held = bool(mask & (1 << bit))
                if held:
                    display.drawFilledRectangle(x, y, 8, 9, 1)
                display.drawText(letter, x + 1, y + 1, 0 if held else 1)
        footer = ('%s %03d/%03d' % (status, received % 1000, sent % 1000)).encode('ascii')
        display.drawText(footer, 0, 32, 1)
        # show() avoids inherited FPS sleeps inside display.update().
        display.show()


def button_mask(kepoco):
    mask = 0
    for bit, name in enumerate(('buttonU', 'buttonD', 'buttonL', 'buttonR', 'buttonA', 'buttonB')):
        if getattr(kepoco, name).pressed():
            mask |= 1 << bit
    return mask

def run(session, max_ms=60000, kepoco=None, stdin=None, stdout=None,
        poller=None, clock=None, sleep=None, on_mask=None):
    import sys
    import time
    if max_ms <= 0:
        raise ValueError('max_ms must be positive')
    if kepoco is None:
        import kepoco
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    if poller is None:
        try:
            import uselect as select
        except ImportError:
            import select
        poller = select.poll()
        poller.register(stdin, select.POLLIN)
    if clock is None:
        start = time.ticks_ms()
        clock = lambda: time.ticks_diff(time.ticks_ms(), start)
    if sleep is None:
        sleep = time.sleep_ms
    peer = Endpoint(session)
    parser = Parser()
    next_sample = 0
    stopping = None
    last_received = 0
    sent = 0
    reason = 'timeout'
    display = getattr(kepoco, 'display', None)
    screen = LinkScreen(display) if display is not None else None
    next_draw = 0
    last_rx_ms = None
    def write(line):
        stdout.write(line.decode('ascii'))
        flush = getattr(stdout, 'flush', None)
        if flush is not None:
            flush()
    try:
        while True:
            now = clock()
            # poll results may have more than two fields. Reads are one-char.
            for item in poller.poll(0):
                if item[0] is stdin and item[1] & 1:
                    for _ in range(256):
                        value = stdin.read(1)
                        if not value:
                            break
                        for packet in parser.feed(value.encode() if isinstance(value, str) else value):
                            previous = peer.highest
                            replies = peer.handle(packet)
                            for reply in replies:
                                write(reply)
                            if packet[0] == INPUT and peer.highest > previous:
                                last_received = peer.remote_mask
                                last_rx_ms = now
                                if on_mask is not None:
                                    on_mask(last_received)
                        if not poller.poll(0):
                            break
            if peer.stopped and stopping is None:
                stopping, reason = now, 'peer-stop'
            if stopping is None and now >= max_ms:
                write(peer.stop(now))
                stopping = now
            if peer.stop_acked or (stopping is not None and now - stopping >= 1100):
                break
            if stopping is None and now >= next_sample and len(peer.pending) < 32:
                write(peer.send(button_mask(kepoco), now))
                sent += 1
                next_sample = now + 33
            try:
                retries = peer.due(now)
            except RuntimeError:
                reason = 'ack-timeout'
                # A missing peer must never strand the board in this loop.
                if STOP_FRAME not in peer.pending:
                    write(peer.stop(now))
                break
            for line in retries:
                write(line)
            if screen is not None and now >= next_draw:
                live = last_rx_ms is not None and now - last_rx_ms <= 500
                status = 'STOP' if stopping is not None else ('LIVE' if live else 'WAIT')
                screen.draw(button_mask(kepoco), peer.remote_mask if live and stopping is None else 0,
                            peer.highest + 1, sent, status)
                next_draw = now + 66
            sleep(2)
    except KeyboardInterrupt:
        reason = 'interrupt'
        try:
            write(peer.stop(clock()))
        except Exception:
            pass
    finally:
        peer.remote_mask = 0
        if screen is not None:
            screen.draw(0, 0, peer.highest + 1, sent,
                        'STOP' if reason in ('peer-stop', 'interrupt') or peer.stop_acked else 'FAIL')
    return {'reason': reason, 'sent': sent, 'received_frame': peer.highest,
            'last_received_mask': last_received, 'parser_errors': parser.errors,
            'rejected': peer.rejected, 'stop_acked': peer.stop_acked}

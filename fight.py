# Imports
"""Small PC-versus-Kepoco fighter. Run: python fight.py --port COM7.
Game, sprites, screen and both loops are here; USB packets are in two helpers.
"""
import sys
import time
try:
    Endpoint
except NameError:
    from protocol import Endpoint, INPUT
try:
    FightParser
except NameError:
    from fight_wire import FightParser, state_line
try:
    import ujson as json
except ImportError:
    import json


# Sprites
Standing = bytearray([255, 239, 231, 52, 128, 52, 231, 239, 255, 1, 1, 1, 1, 1, 1, 1, 1, 1])
HitRight = bytearray([255, 255, 241, 52, 128, 52, 247, 247, 247, 1, 1, 1, 1, 1, 1, 1, 1, 1])
HitLeft = bytearray([247, 247, 247, 52, 128, 52, 241, 255, 255, 1, 1, 1, 1, 1, 1, 1, 1, 1])
Blocking = bytearray([255, 255, 241, 52, 128, 52, 241, 255, 255, 1, 1, 1, 1, 1, 1, 1, 1, 1])
LeadRight = bytearray([255, 255, 113, 52, 128, 52, 231, 255, 255, 1, 1, 1, 1, 1, 1, 1, 1, 1])
LeadLeft = bytearray([255, 255, 231, 52, 128, 52, 113, 255, 255, 1, 1, 1, 1, 1, 1, 1, 1, 1])
SPRITES = {'Standing': Standing, 'HitRight': HitRight, 'HitLeft': HitLeft, 'Blocking': Blocking, 'LeadRight': LeadRight, 'LeadLeft': LeadLeft}
INVERTED = {name: bytearray((value ^ 255 for value in sprite)) for name, sprite in SPRITES.items()}


# Game rules
FPS = 30
HIT_COOLDOWN = 21       # 0.7 seconds at 30 frames per second
BLOCK_LOCKOUT = 5       # Cannot block immediately after a punch
PARRY_COOLDOWN = 300    # 10 seconds
FLASH_FRAMES = 3
ATTACK = 16
BLOCK = 32

# The fist is on row 3. A zero bitmap bit is part of the fighter.
# Check the fist pixel and its four neighbours, not empty sprite padding.
def fist_touches(attacker, positions, target_pose):
    fist_x = positions[attacker]
    if attacker == 0:
        fist_x += 8
    target_x = positions[1 - attacker]
    sprite = SPRITES[target_pose]
    for x, y in ((fist_x, 3), (fist_x - 1, 3), (fist_x + 1, 3), (fist_x, 2), (fist_x, 4)):
        column = x - target_x
        if 0 <= column < 9:
            if not sprite[column] & 1 << y:
                return True
    return False

class Fight:

    def __init__(self):
        self.frame = 0
        self.hp = [100, 100]
        self.x = [18, 45]
        self.flash = [0, 0]
        self.hit_ready = [0, 0]
        self.block_ready = [0, 0]
        self.parry_ready = [0, 0]
        self.blocking = [False, False]
        self.pose = ['Standing', 'Standing']
        self.hit_until = [0, 0]
        self.over = False

    def step(self, p1, p2):
        for i in range(2):
            self.flash[i] = max(0, self.flash[i] - 1)
        if self.over:
            return
        masks = [p1, p2]
        attacks = [False, False]
        parry = [False, False]
        for i in range(2):
            if masks[i] & ATTACK and self.frame >= self.hit_ready[i]:
                attacks[i] = True
                self.hit_ready[i] = self.frame + HIT_COOLDOWN
                self.block_ready[i] = self.frame + BLOCK_LOCKOUT
                self.hit_until[i] = self.frame + 4
            was_blocking = self.blocking[i]
            self.blocking[i] = bool(masks[i] & BLOCK and self.frame >= self.block_ready[i])
            parry[i] = self.blocking[i] and (not was_blocking) and (self.frame >= self.parry_ready[i])
        for i in range(2):
            move = int(bool(masks[i] & 8)) - int(bool(masks[i] & 4))
            if self.blocking[i] or masks[i] & ATTACK:
                move = 0
            # Seven pixels between centres lets the actual drawn arms touch.
            self.x[i] = max(0, min(63, self.x[i] + move))
            if i == 0:
                self.x[i] = min(self.x[i], self.x[1] - 7)
            else:
                self.x[i] = max(self.x[i], self.x[0] + 7)
            self.pose[i] = 'Standing'
            if self.blocking[i]:
                self.pose[i] = 'Blocking'
            elif self.frame < self.hit_until[i]:
                self.pose[i] = 'HitRight' if i == 0 else 'HitLeft'
            elif move:
                self.pose[i] = 'LeadRight' if move > 0 else 'LeadLeft'
        # Decide both hits before subtracting health, so punches can trade.
        damage = [0, 0]
        for i in range(2):
            other = 1 - i
            if attacks[i] and fist_touches(i, self.x, self.pose[other]):
                if parry[other]:
                    self.parry_ready[other] = self.frame + PARRY_COOLDOWN
                    self.flash = [FLASH_FRAMES, FLASH_FRAMES]
                else:
                    damage[other] = 3 if self.blocking[other] else 7
        for i in range(2):
            if damage[i]:
                self.hp[i] = max(0, self.hp[i] - damage[i])
                self.flash[i] = FLASH_FRAMES
        self.over = 0 in self.hp
        self.frame += 1

    def snapshot(self):
        return [self.frame, self.hp[:], self.x[:], self.pose[:], self.flash[:], [max(0, n - self.frame) for n in self.parry_ready], self.over]


# Drawing
def draw_fight(display, state, player, waiting=False):
    frame, hp, x, pose, flash, parry, over = state
    background = 0 if flash[player] else 1
    ink = 1 - background
    display.fill(background)
    display.drawText(('1:%03d 2:%03d' % (hp[0], hp[1])).encode(), 0, 0, ink)
    for i in (0, 1):
        display.drawFilledRectangle(i * 39, 8, hp[i] * 30 // 100, 2, ink)
        sprite = SPRITES[pose[i]]
        if hasattr(display, 'blit'):
            image = sprite if background else INVERTED[pose[i]]
            display.blit(image, x[i], 20, 9, 9, background, 0, 0)
        else:
            for col in range(9):
                for row in range(9):
                    byte = sprite[col + row // 8 * 9]
                    if not byte & 1 << row % 8:
                        display.setPixel(x[i] + col, 20 + row, ink)
    display.drawFilledRectangle(0, 30, 72, 1, ink)
    if waiting:
        label = b'LINK WAIT'
    elif over:
        label = b'DRAW' if hp == [0, 0] else b'P1 WINS' if hp[1] == 0 else b'P2 WINS'
    else:
        label = b''
    if label:
        display.drawText(label, 0, 11, ink)
    display.drawText(('P%02d P%02d YOU%d' % ((parry[0] + 29) // 30, (parry[1] + 29) // 30, player + 1)).encode(), 0, 32, ink)
    display.show()


# Skip unchanged pictures
class FightScreen:

    def __init__(self, display, player):
        self.display = display
        self.player = player
        self.last_picture = None
        self.draws = 0

    def draw(self, state, waiting=False):
        frame, hp, x, pose, flash, parry, over = state
        picture = (tuple(hp), tuple(x), tuple(pose), bool(flash[self.player]), tuple(((value + 29) // 30 for value in parry)), over, waiting)
        if picture != self.last_picture:
            draw_fight(self.display, state, self.player, waiting)
            self.last_picture = picture
            self.draws += 1


# Kepoco loop
def run_device(session, max_ms=600000, kepoco=None, stdin=None, stdout=None, poller=None, clock=None, sleep=None):
    import sys, time
    if kepoco is None:
        import kepoco
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    if poller is None:
        import select
        poller = select.poll()
        poller.register(stdin, select.POLLIN)
    if clock is None:
        start = time.ticks_ms()
        clock = lambda: time.ticks_diff(time.ticks_ms(), start)
    if sleep is None:
        sleep = time.sleep_ms
    peer = Endpoint(session)
    parser = FightParser()
    screen = FightScreen(kepoco.display, 0)
    state = [0, [100, 100], [18, 45], ['Standing', 'Standing'], [0, 0], [0, 0], False]
    sequence = -1
    states = 0
    last_state = -1000
    next_input = 0
    last_button_mask = None
    last_button_send = -1000
    next_draw = 0
    stopping = None
    reason = 'timeout'

    outgoing = []

    def write(line):
        outgoing.append(line)

    def flush():
        if outgoing:
            stdout.write(b''.join(outgoing).decode('ascii'))
            outgoing.clear()
    try:
        while True:
            now = clock()
            picture_changed = False
            received_flash = [0, 0]
            for item in poller.poll(0):
                if item[0] is stdin and item[1] & 1:
                    for _ in range(256):
                        value = stdin.read(1)
                        if not value:
                            break
                        for kind, packet in parser.feed(value.encode() if isinstance(value, str) else value):
                            if kind == 'input':
                                for reply in peer.handle(packet):
                                    write(reply)
                            elif packet[0] == session and packet[1] == 'state':
                                number, new_state, waiting = packet[2]
                                if number > sequence:
                                    sequence = number
                                    state = new_state
                                    states += 1
                                    last_state = now
                                    picture_changed = True
                                    for i in (0, 1):
                                        received_flash[i] = max(received_flash[i], state[4][i])
                                write(state_line(session, 'ack', number))
                        if not poller.poll(0):
                            break
            if picture_changed:
                state[4] = received_flash
                screen.draw(state, waiting)
            if peer.stopped and stopping is None:
                stopping = now
                reason = 'peer-stop'
            if stopping is None and now >= max_ms:
                write(peer.stop(now))
                stopping = now
            if peer.stop_acked or (stopping is not None and now - stopping >= 1100):
                break
            if stopping is None and now >= next_input and (len(peer.pending) < 32):
                mask = 0
                for bit, name in enumerate(('buttonU', 'buttonD', 'buttonL', 'buttonR', 'buttonA', 'buttonB')):
                    if getattr(kepoco, name).pressed():
                        mask |= 1 << bit
                if mask != last_button_mask or now - last_button_send >= 100:
                    write(peer.send(mask, now))
                    last_button_mask = mask
                    last_button_send = now
                next_input = now + 33
            if now - last_state > 500 and now >= next_draw:
                state[4] = [0, 0]
                screen.draw(state, True)
                next_draw = now + 100
            try:
                for line in peer.due(now):
                    write(line)
            except RuntimeError:
                reason = 'ack-timeout'
                break
            flush()
            sleep(2)
    except KeyboardInterrupt:
        reason = 'interrupt'
    finally:
        flush()
        peer.remote_mask = 0
        state[4] = [0, 0]
        screen.draw(state, True)
    return {'states': states, 'draws': screen.draws, 'reason': reason, 'parser_errors': parser.errors}


# PC window and loop
class Window:

    def __init__(self):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title('Kepoco Fight - PC player 2')
        self.scale = 8
        self.canvas = tk.Canvas(self.root, width=72 * 8, height=40 * 8, highlightthickness=0)
        self.canvas.pack()
        tk.Label(self.root, text='Move: A/D or arrows | J: hit | K: block | R: restart | Esc: quit').pack()
        self.keys = set()
        self.closed = False
        self.restart = False
        self.images = {}
        self.root.bind('<KeyPress>', self.press)
        self.root.bind('<KeyRelease>', lambda e: self.keys.discard(e.keysym.lower()))
        self.root.bind('<FocusOut>', lambda e: self.keys.clear())
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def press(self, event):
        key = event.keysym.lower()
        self.keys.add(key)
        if key == 'escape':
            self.close()
        if key == 'r':
            self.restart = True

    def close(self):
        self.closed = True

    def service(self):
        if not self.closed:
            self.root.update_idletasks()
            self.root.update()

    def mask(self, elapsed):
        if self.closed:
            return None
        mapping = (('w', 'up'), ('s', 'down'), ('a', 'left'), ('d', 'right'), ('j',), ('k',))
        return sum((1 << i for i, keys in enumerate(mapping) if any((k in self.keys for k in keys))))

    def fill(self, c):
        self.canvas.delete('all')
        self.canvas.configure(bg='white' if c else 'black')

    def drawFilledRectangle(self, x, y, w, h, c):
        if w and h:
            s = self.scale
            self.canvas.create_rectangle(x * s, y * s, (x + w) * s, (y + h) * s, fill='white' if c else 'black', outline='')

    def blit(self, sprite, x, y, width, height, transparent, mirror_x, mirror_y):
        # Make each tiny Tk image once, then draw it with one canvas item.
        key = id(sprite)
        if key not in self.images:
            image = self.tk.PhotoImage(width=9, height=9)
            for column in range(9):
                for row in range(9):
                    value = (sprite[column + (row // 8) * 9] >> (row % 8)) & 1
                    if value != transparent:
                        image.put('white' if value else 'black', (column, row))
            self.images[key] = image.zoom(self.scale, self.scale)
        self.canvas.create_image(x * self.scale, y * self.scale,
                                 image=self.images[key], anchor='nw')

    def setPixel(self, x, y, c):
        self.drawFilledRectangle(x, y, 1, 1, c)

    def drawText(self, text, x, y, c):
        self.canvas.create_text(x * 8, y * 8, text=text.decode(), anchor='nw', fill='white' if c else 'black', font=('Courier', -56, 'bold'))

    def show(self):
        pass

def launch_fight(port, session, max_ms):
    from mpremote.transport_serial import SerialTransport
    from pathlib import Path
    folder = Path(__file__).parent
    source = '\n'.join(((folder / name).read_text(encoding='utf-8') for name in ('protocol.py', 'fight_wire.py', 'fight.py')))
    source += '\nrun_device(%d,%d)\n' % (session, max_ms)
    owner = SerialTransport(port, baudrate=115200, timeout=2)
    try:
        owner.enter_raw_repl(soft_reset=False, timeout_overall=5)
        owner.exec_raw_no_follow(source)
        owner.serial.timeout = 0
        owner.serial.write_timeout = 2
        return owner
    except BaseException:
        owner.close()
        raise

def run_match(connection, session, seconds, source, display=None, service=None, game=None, capture=None):
    game = game or Fight()
    peer = Endpoint(session)
    parser = FightParser()
    screen = FightScreen(display, 1) if display is not None else None
    start = time.monotonic()
    next_step = 0
    last_input = -1000
    sequence = 0
    acks = set()
    last_ack = -1
    last_sent_mask = None
    last_send_ms = -1000
    last_state_picture = None
    last_state_send = -1000
    log = open(capture, 'w', encoding='utf-8') if capture else None
    outgoing = []

    def write(line):
        outgoing.append(line)

    def flush():
        if outgoing:
            data = b''.join(outgoing)
            outgoing.clear()
            if connection.write(data) != len(data):
                raise OSError('short USB write')

    def pump(now):
        nonlocal last_input, last_ack
        count = connection.in_waiting
        data = connection.read(min(count, 4096)) if count else b''
        for kind, packet in parser.feed(data):
            if kind == 'input':
                previous = peer.highest
                for reply in peer.handle(packet):
                    write(reply)
                if peer.highest > previous:
                    last_input = now
            elif packet[0] == session and packet[1] == 'ack' and isinstance(packet[2], int):
                last_ack = max(last_ack, packet[2])
                acks.add(packet[2])
        if len(acks) > 1024:
            acks.clear()
    try:
        while time.monotonic() - start < seconds and (not peer.stopped):
            elapsed = time.monotonic() - start
            now = int(elapsed * 1000)
            if service:
                service()
            mask = source(elapsed)
            if mask is None:
                break
            pump(now)
            if peer.stopped:
                break
            if elapsed >= next_step:
                waiting = now - last_input > 500
                if getattr(display, 'restart', False):
                    game = Fight()
                    display.restart = False
                burst_flash = [0, 0]
                if not waiting:
                    # Keep cooldown timing independent of USB drawing speed.
                    due = min(8, int((elapsed - next_step) * FPS) + 1)
                    for _ in range(due):
                        game.step(peer.remote_mask, mask)
                        burst_flash = [max(burst_flash[i], game.flash[i]) for i in (0, 1)]
                    next_step += due / FPS
                else:
                    next_step = elapsed + 1 / FPS
                if len(peer.pending) < 32 and (mask != last_sent_mask or now - last_send_ms >= 100):
                    write(peer.send(mask, now))
                    last_sent_mask = mask
                    last_send_ms = now
                snapshot = game.snapshot()
                snapshot[4] = burst_flash
                # Ignore the frame counter: unchanged art only needs a heartbeat.
                hp, positions, poses = snapshot[1], snapshot[2], snapshot[3]
                flashes, parry = snapshot[4], snapshot[5]
                picture = (tuple(hp), tuple(positions), tuple(poses),
                           bool(flashes[0]), bool(flashes[1]),
                           tuple((value + 29) // 30 for value in parry), snapshot[6], waiting)
                if picture != last_state_picture or now - last_state_send >= 100:
                    write(state_line(session, 'state', [sequence, snapshot, waiting]))
                    last_state_picture = picture
                    last_state_send = now
                    sequence += 1
                if screen:
                    screen.draw(snapshot, waiting)
                if log:
                    log.write(json.dumps({'sequence': sequence, 'p1': peer.remote_mask, 'p2': mask, 'waiting': waiting, 'state': snapshot}) + '\n')
            for line in peer.due(now):
                write(line)
            flush()
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        if not peer.stopped:
            write(peer.stop(int((time.monotonic() - start) * 1000)))
            flush()
            deadline = time.monotonic() + 1.2
            while not peer.stop_acked and time.monotonic() < deadline:
                now = int((time.monotonic() - start) * 1000)
                pump(now)
                for line in peer.due(now):
                    write(line)
                flush()
                time.sleep(0.002)
        if log:
            log.close()
    return {'game_frames': game.frame, 'health': game.hp, 'state_snapshots': sequence, 'states_acked': len(acks), 'last_state_acked': last_ack, 'kepoco_input_frame': peer.highest, 'stop_acked': peer.stop_acked, 'parser_errors': parser.errors,
            'screen_draws': screen.draws if screen else 0}

def script_source(text, interval):
    masks = [int(value.strip(), 0) for value in text.split(',')]
    if interval <= 0 or any(mask < 0 or mask > 63 for mask in masks):
        raise ValueError('invalid scripted input')
    def read_mask(elapsed):
        position = min(int(elapsed / interval), len(masks) - 1)
        return masks[position]
    return read_mask


def main():
    import argparse, secrets
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='COM7')
    parser.add_argument('--seconds', type=float, default=600)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--script', default='0')
    parser.add_argument('--interval', type=float, default=0.2)
    parser.add_argument('--capture', default=None)
    args = parser.parse_args()
    if not 0 < args.seconds <= 3600:
        parser.error('--seconds must be 0..3600')
    window = None if args.headless else Window()
    source = script_source(args.script, args.interval) if args.headless else window.mask
    owner = None
    try:
        session = secrets.randbelow(4294967295) + 1
        owner = launch_fight(args.port, session, int(args.seconds * 1000) + 3000)
        result = run_match(owner.serial, session, args.seconds, source, window, window.service if window else None, capture=args.capture)
        print(json.dumps(result, indent=2))
        return 0 if result['stop_acked'] and result['last_state_acked'] >= 0 else 1
    finally:
        if owner:
            try:
                time.sleep(1.2)
                owner.serial.timeout = 1
                owner.enter_raw_repl(soft_reset=False, timeout_overall=3)
                owner.exit_raw_repl()
            finally:
                owner.close()
        if window:
            window.root.destroy()
if __name__ == '__main__' and sys.implementation.name != 'micropython':
    raise SystemExit(main())

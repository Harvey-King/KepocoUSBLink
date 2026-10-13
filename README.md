# KepocoUSBLink

PC-versus-Kepoco fighter and standalone USB input-link tools. The PC is
the USB host. Handheld programs launch temporarily in RAM: no file
uploads, firmware changes or startup changes.

## Setup

Use Windows Python with Tkinter, then run:

    python -m pip install -r requirements.txt
    python pc_link.py --list

Replace COM_KEPOCO below with the identified Kepoco port. Close any
editor or REPL using it first. Launch interrupts the handheld's current
program; cleanup attempts to restore the friendly REPL without rebooting.

## Fighter

    python fight.py --port COM_KEPOCO

Player 1 is the Kepoco; player 2 is the PC and match authority.

- Kepoco: Left/Right move, A punches, B blocks.
- PC: A/D or Left/Right move, J punches, K blocks.
- Focus the PC window. R restarts; Escape exits.
- Hold punch to repeat on cooldown; hold block for defence.

Punches must contact visible sprite pixels. Players start with 100 health;
normal hits do 7 damage, blocked hits do 3. Punch cooldown is 21 frames,
with five frames of block lockout. The first effective block frame can
parry, preventing damage and flashing both views; successful parries have
a 300-frame cooldown. Simultaneous punches can trade; simultaneous
knockouts draw. Simulation runs at 30 frames per second; link loss pauses
it. This is a PC-authoritative prototype, not symmetric lockstep.

Optional recording:

    python fight.py --port COM_KEPOCO --capture fight-capture.jsonl

## Standalone input link

These commands exchange held button masks, not game state:

    python pc_link.py --port COM_KEPOCO --launch --script "0,16,0,32,0,48,0" --interval 0.1 --capture usb-script.jsonl
    python pc_link.py --port COM_KEPOCO --launch --seconds 60 --capture usb-keys.jsonl

PC controls: W/Up, S/Down, A/Left, D/Right, J/A button, K/B button;
Escape quits. This test reads global Windows held-key state: do not type
private material in other windows while it runs. The handheld monitor
shows independent KP and PC rows; stale PC input clears after 500 ms.
Masks are U=1, D=2, L=4, R=8, A=16, B=32.

Use --launch normally. Without it, the device must already be running
the input runner with the same explicit --session; a friendly REPL is
not an input endpoint. Runs are bounded, with STOP/ACK cleanup. Captures
overwrite their destination and include retransmissions, so they are
wire observations, not a list of accepted packets.

## Source files

- fight.py: game, sprites, rendering, PC window and handheld loop.
- fight_wire.py: fighter state-packet framing.
- protocol.py: bounded parser, CRC, sessions and ACK/retry logic.
- pc_link.py: Windows input peer, port discovery and RAM launcher.
- device_link.py: handheld input peer and USB input monitor.
- selftest.py: localhost transport test with fake buttons and packet loss.
- tests/: gameplay, rendering and bidirectional transport regression tests.

The old fight_pc.py, fight_core.py, fight_display.py and fight_device.py
compatibility wrappers are removed. Run and import fight.py directly.

The input wire format is KSLT1: plus 28 hexadecimal characters and LF.
Its 14-byte payload contains kind (INPUT=1, ACK=2, STOP=3), a nonzero
32-bit session, a 32-bit frame, a six-bit mask and IEEE CRC32. Integers
are little-endian. ACK/STOP masks are zero; STOP reserves frame
0xffffffff. Each direction has a 32-sample pending/duplicate window,
100-ms retries and at most ten transmissions. CRC detects corruption,
not malicious input. This subset is not the proposed KSL1 fighter
protocol. See the source for exact validation rules.

## Verify without hardware

    python -B -m unittest discover -s tests -v
    python -B selftest.py --capture selftest-capture.jsonl --report selftest-report.json

Host tests exercise gameplay, screen bounds, real localhost connections,
packet-loss recovery and the CLI's pyserial socket backend. They do not
prove physical button behavior, visual display quality or USB frame rate
on a connected handheld. Physical-device testing is a separate step.

Generated captures, reports, caches and previews are excluded by
.gitignore. Host dependencies need no installation on the device.


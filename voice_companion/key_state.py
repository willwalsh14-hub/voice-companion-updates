"""Which modifier keys a Tk key event says are held down.

Tk puts the modifier keys and the lock keys in one number, ``event.state``.
The bits mean different things on each platform:

- Windows (Tk's win/tkWinX.c): Shift 0x1, Caps Lock on 0x2, Control 0x4,
  Num Lock on 0x8 (Mod1), Scroll Lock on 0x20 (Mod3), Alt 0x20000 (ALT_MASK).
- X11 (Linux): Shift 0x1, Caps Lock 0x2, Control 0x4, Alt 0x8 (Mod1),
  Num Lock usually 0x10 (Mod2).

So on Windows 0x8 only means Num Lock is on. It must never count as Alt:
with Num Lock on, every plain key would look like Alt plus that key.
Caps Lock, Num Lock and Scroll Lock are never treated as held modifiers here.
"""
import sys

# Read at call time so tests can check each platform's rules.
PLATFORM = sys.platform

SHIFT = 0x1
CAPS_LOCK = 0x2
CONTROL = 0x4
MOD1 = 0x8           # Num Lock on Windows; Alt on X11.
SCROLL_LOCK = 0x20   # Mod3: Scroll Lock on Windows.
WINDOWS_ALT = 0x20000  # Tk's ALT_MASK, set for Alt on Windows.


def _windows(platform=None):
    return (PLATFORM if platform is None else platform).startswith('win')


def alt_mask(platform=None):
    """The state bits that mean Alt is held on this platform."""
    return WINDOWS_ALT if _windows(platform) else MOD1 | WINDOWS_ALT


# The state value Tk gives a key pressed with Alt held, on this platform.
ALT_STATE = WINDOWS_ALT if sys.platform.startswith('win') else MOD1


def alt_held(state, platform=None):
    return bool(int(state or 0) & alt_mask(platform))


def control_held(state):
    return bool(int(state or 0) & CONTROL)


def shift_held(state):
    return bool(int(state or 0) & SHIFT)


def modifiers(state, platform=None):
    """Return (control, shift, alt) for a Tk event state."""
    return control_held(state), shift_held(state), alt_held(state, platform)

"""Num Lock, Caps Lock and Scroll Lock must never look like Alt.

On Windows, Tk sets bit 0x8 in event.state while Num Lock is on, and 0x20000
while Alt is held. On Linux (X11), 0x8 is Alt. These tests feed the state
values Tk really sends on each platform.
"""
import os
import re
import unittest
from unittest.mock import patch

import key_state
from input_parity import chord, screen_key
from screen_menus import overlay_key
from web_modes import page_key, toggle_chord, window_request

NUM_LOCK_WIN = 0x8
CAPS_LOCK = 0x2
SCROLL_LOCK_WIN = 0x20
ALT_WIN = 0x20000
ALT_X11 = 0x8
NUM_LOCK_X11 = 0x10
HERE = os.path.dirname(os.path.abspath(__file__))


def on(platform):
    return patch.object(key_state, 'PLATFORM', platform)


class WindowsStateTests(unittest.TestCase):
    def setUp(self):
        patcher = on('win32')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lock_keys_are_not_modifiers(self):
        for lock in (NUM_LOCK_WIN, CAPS_LOCK, SCROLL_LOCK_WIN, NUM_LOCK_WIN | CAPS_LOCK | SCROLL_LOCK_WIN):
            with self.subTest(state=hex(lock)):
                self.assertEqual(key_state.modifiers(lock), (False, False, False))

    def test_alt_bit_is_alt_with_or_without_num_lock(self):
        self.assertTrue(key_state.alt_held(ALT_WIN))
        self.assertTrue(key_state.alt_held(ALT_WIN | NUM_LOCK_WIN))
        self.assertEqual(key_state.modifiers(4 | 1 | ALT_WIN | NUM_LOCK_WIN), (True, True, True))

    def test_menu_letters_work_with_num_lock_on(self):
        self.assertEqual(overlay_key('o', 'o', NUM_LOCK_WIN), 'Letter:o')
        self.assertEqual(overlay_key('r', 'r', NUM_LOCK_WIN | CAPS_LOCK), 'Letter:r')
        self.assertEqual(overlay_key('Down', '', NUM_LOCK_WIN), 'Down')
        self.assertIsNone(overlay_key('o', 'o', ALT_WIN | NUM_LOCK_WIN))   # Alt+O still opens Options

    def test_web_keys_with_num_lock_on(self):
        self.assertFalse(toggle_chord('space', 4 | NUM_LOCK_WIN))
        self.assertTrue(toggle_chord('space', 4 | ALT_WIN | NUM_LOCK_WIN))
        self.assertEqual(page_key('k', 'k', NUM_LOCK_WIN), 'k')
        self.assertEqual(page_key('Left', '', NUM_LOCK_WIN), 'ArrowLeft')
        self.assertEqual(page_key('Left', '', ALT_WIN), 'Alt+ArrowLeft')
        context = {'web_app_mode': True}
        self.assertEqual(window_request('Right', '', NUM_LOCK_WIN, context), 'Web:Pass:ArrowRight')
        self.assertIsNone(window_request('Right', '', ALT_WIN | NUM_LOCK_WIN, context))
        slider = {'web_slider': True}
        self.assertEqual(window_request('Right', '', NUM_LOCK_WIN, slider), 'Web:Slider:Right')

    def test_screen_keys_with_num_lock_on(self):
        self.assertEqual(chord('Next', NUM_LOCK_WIN), 'Next')
        self.assertEqual(chord('F5', NUM_LOCK_WIN | SCROLL_LOCK_WIN), 'F5')
        self.assertEqual(chord('n', 4 | NUM_LOCK_WIN), 'Ctrl+N')
        self.assertIsNone(chord('Next', ALT_WIN))
        # Control+S saves a note with Num Lock on; Control+Alt+S is left alone.
        self.assertEqual(screen_key('note', 's', 4 | NUM_LOCK_WIN, {}), 'Ctrl+S')
        self.assertIsNone(screen_key('note', 's', 4 | ALT_WIN | NUM_LOCK_WIN, {}))


class LinuxStateTests(unittest.TestCase):
    """On X11, Mod1 (0x8) is Alt; Num Lock is Mod2 (0x10). Behaviour there is unchanged."""
    def setUp(self):
        patcher = on('linux')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mod1_is_alt_and_num_lock_is_not(self):
        self.assertTrue(key_state.alt_held(ALT_X11))
        self.assertFalse(key_state.alt_held(NUM_LOCK_X11 | CAPS_LOCK))
        self.assertIsNone(overlay_key('o', 'o', ALT_X11))
        self.assertEqual(overlay_key('o', 'o', NUM_LOCK_X11), 'Letter:o')
        self.assertTrue(toggle_chord('space', 4 | ALT_X11))


class PlatformAltStateTests(unittest.TestCase):
    def test_alt_state_is_what_tk_sends_here(self):
        self.assertTrue(key_state.alt_held(key_state.ALT_STATE))
        self.assertEqual(key_state.ALT_STATE, ALT_WIN if os.name == 'nt' else ALT_X11)


class NoRawAltBitTests(unittest.TestCase):
    """Every Alt check goes through key_state, so Num Lock can never be Alt again."""
    def test_no_module_tests_state_bits_for_alt_directly(self):
        raw = re.compile(r'state\s*&\s*\(?[^)\n]*\b(?:8|0x20000)\b')
        for name in ('app_window.py', 'screen_menus.py', 'web_modes.py', 'input_parity.py'):
            with open(os.path.join(HERE, name), encoding='utf-8') as source:
                found = [line.strip() for line in source if raw.search(line)]
            with self.subTest(module=name):
                self.assertEqual(found, [])

    def test_tests_send_the_platform_alt_state_not_the_x11_bit(self):
        # A test that sends 8 (or 12 for Control+Alt) means Alt only on Linux.
        # On Windows Tk, 8 is Num Lock, so such a test checks the wrong key there.
        # Tests send key_state.ALT_STATE instead.
        literal = r'(0x[0-9a-fA-F]+|\d+)'
        patterns = (
            re.compile(r'\bstate\s*=\s*' + literal + r'\b'),            # event_generate(..., state=12)
            re.compile(r"\('[\w]+',\s*" + literal + r",\s*'"),           # ('Right', 12, 'Web:Table:Right')
            re.compile(r'(?<![\w.])' + literal + r'\s*\|\s*' + literal + r'\b'),  # screen_key(..., 8|4, ...)
        )
        problems = []
        for name in sorted(os.listdir(HERE)):
            if not (name.startswith('test_') and name.endswith('.py')) or name == 'test_key_state.py':
                continue
            with open(os.path.join(HERE, name), encoding='utf-8') as source:
                for number, line in enumerate(source, 1):
                    for pattern in patterns:
                        for match in pattern.finditer(line):
                            values = [int(value, 0) for value in match.groups() if value]
                            if any(0 < value < 0x100 and value & key_state.MOD1 for value in values):
                                problems.append(f'{name}:{number}: {line.strip()}')
        self.assertEqual(problems, [])


if __name__ == '__main__':
    unittest.main()

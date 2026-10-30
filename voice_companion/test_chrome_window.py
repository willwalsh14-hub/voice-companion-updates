"""Guided Chrome's window kept out of sight, for "Run the browser in the background".

Will, October 29, 2026: headless Chrome made Google show its "unusual traffic" page
and YouTube say "Something went wrong". The background choice now runs a real
Chrome whose window is minimized and off the taskbar. Only the Chrome Voice
Companion started is touched, never the user's own, and the keyboard goes back
to where it was. Windows is faked here; test_browser_window_live checks it for real.
"""
import unittest

import chrome_window
from chrome_window import GuidedChromeWindow, started_browsers
from window_hiding import Window

ME = 100


def table(*extra):
    rows = {ME: (1, 'voicecompanion.exe'), 200: (ME, 'node.exe'),             # our Playwright driver
            300: (50, 'chrome.exe'), 301: (300, 'chrome.exe'),                 # the user's own Chrome
            50: (1, 'explorer.exe')}
    rows.update(dict(extra))
    return rows


class FakeDesktop:
    def __init__(self):
        self.list = []; self.tucks = []; self.front = 900; self.brought = []; self.parked = set()

    def windows(self, pids=None):
        return [w for pid, w in self.list if pids is None or pid in pids]

    def park(self, handle):
        # Off the taskbar and off the screen, but never minimized: a minimized Chrome stops drawing.
        self.tucks.append(handle); self.parked.add(handle)
        self.list = [(pid, w._replace(tool=True, minimized=False) if w.handle == handle else w) for pid, w in self.list]
        return 0

    def off_screen(self, handle):
        return handle in self.parked

    def foreground(self):
        return self.front

    def bring_forward(self, handle):
        self.brought.append(handle); self.front = handle; return True


def window(handle, tool=False, minimized=False):
    return Window(handle, chrome_window.CHROME_CLASS, 'New Tab - Google Chrome', minimized, False, tool)


class StartedBrowserTests(unittest.TestCase):
    def test_only_chrome_under_our_driver_counts(self):
        rows = table((400, (200, 'chrome.exe')), (401, (400, 'chrome.exe')), (402, (400, 'chrome.exe')))
        self.assertEqual(started_browsers(rows, ME), {400})

    def test_the_users_chrome_started_by_us_through_explorer_is_not_ours(self):
        self.assertEqual(started_browsers(table((500, (50, 'chrome.exe'))), ME), set())

    def test_a_loop_in_the_parent_list_ends(self):
        self.assertEqual(started_browsers({7: (8, 'chrome.exe'), 8: (7, 'node.exe')}, ME), set())


class KeeperTests(unittest.TestCase):
    def setUp(self):
        self.desktop = FakeDesktop()
        self.rows = table()
        self.keeper = GuidedChromeWindow(self.desktop, lambda: self.rows, ME)
        self.addCleanup(self.keeper.stop)

    def launch(self):
        self.keeper.before_launch()
        self.rows = table((400, (200, 'chrome.exe')), (401, (400, 'chrome.exe')))
        self.desktop.list = [(300, window(30)), (400, window(40))]   # the user's Chrome, and ours
        self.desktop.front = 40                                        # Chrome took the keyboard
        return self.keeper.after_launch()

    def test_only_the_new_chrome_is_hidden_and_the_keyboard_comes_back(self):
        self.assertTrue(self.launch())
        self.assertEqual(self.desktop.tucks, [40])
        self.assertEqual(self.desktop.brought, [900])

    def test_a_chrome_already_running_under_us_is_left_alone(self):
        self.rows = table((450, (200, 'chrome.exe')))     # e.g. the media player's Chrome
        self.keeper.before_launch()
        self.rows = table((450, (200, 'chrome.exe')), (400, (200, 'chrome.exe')))
        self.desktop.list = [(450, window(45)), (400, window(40))]
        self.keeper.after_launch()
        self.assertEqual(self.desktop.tucks, [40])

    def test_a_new_pop_up_window_is_hidden_and_a_hidden_one_is_left(self):
        self.launch()
        self.desktop.list.append((400, window(41)))
        self.assertEqual(self.keeper.poll(), 1)
        self.assertEqual(self.keeper.poll(), 0)
        self.assertEqual(self.desktop.tucks, [40, 41])

    def test_a_window_chrome_brings_up_again_is_hidden_again(self):
        self.launch()
        self.desktop.parked.discard(40)   # Chrome moved it back on the screen
        self.assertEqual(self.keeper.poll(), 1)

    def test_a_minimized_window_is_restored_off_the_screen(self):
        # Will, October 30, 2026: YouTube said "Something went wrong" in a minimized Chrome.
        self.launch()
        self.desktop.list = [(pid, w._replace(minimized=True) if w.handle == 40 else w) for pid, w in self.desktop.list]
        self.assertEqual(self.keeper.poll(), 1)
        self.assertFalse(dict((w.handle, w) for _, w in self.desktop.list)[40].minimized)

    def test_a_page_brought_to_the_front_gives_the_keyboard_back(self):
        self.launch()
        self.desktop.brought.clear()
        # YouTube's video page is brought to the front: Chrome restores its window and takes the keys.
        self.desktop.parked.discard(40)
        self.desktop.front = 40
        self.assertEqual(self.keeper.poll(), 1)
        self.assertEqual(self.desktop.brought, [900])

    def test_a_hidden_window_with_the_keyboard_gives_it_back(self):
        self.launch()
        self.desktop.brought.clear()
        self.desktop.front = 40
        self.assertEqual(self.keeper.poll(), 0)
        self.assertEqual(self.desktop.brought, [900])

    def test_the_users_own_chrome_keeps_the_keyboard(self):
        self.launch()
        self.desktop.brought.clear()
        self.desktop.front = 30
        self.keeper.poll()
        self.assertEqual(self.desktop.brought, [])

    def test_the_keyboard_is_not_moved_when_it_never_left(self):
        self.keeper.before_launch()
        self.rows = table((400, (200, 'chrome.exe')))
        self.desktop.list = [(400, window(40))]
        self.keeper.after_launch()
        self.assertEqual(self.desktop.brought, [])

    def test_no_new_chrome_found_hides_nothing(self):
        self.keeper.before_launch()
        self.desktop.list = [(300, window(30))]
        self.assertFalse(self.keeper.after_launch())
        self.assertEqual(self.desktop.tucks, [])


if __name__ == '__main__':
    unittest.main()

"""One Playwright driver per thread (Will, October 2026).

A downloaded song would not play while a web page was open: the media player started
a second Playwright driver on the same thread, which Playwright refuses."""
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

import shared_playwright


def on_fresh_thread(test):
    """Run in a thread of its own: other tests in the same run may have left a
    Playwright driver or an asyncio loop on the main thread."""
    def run(self):
        import threading
        errors = []
        def body():
            try: test(self)
            except BaseException as error: errors.append(error)
        thread = threading.Thread(target=body); thread.start(); thread.join(120)
        if errors: raise errors[0]
    run.__name__ = test.__name__
    return run


@unittest.skipUnless(HAVE_PLAYWRIGHT, 'Playwright is not installed')
class SharedDriverTests(unittest.TestCase):
    @on_fresh_thread
    def test_web_browser_and_media_player_can_both_hold_a_driver(self):
        folder = Path(tempfile.mkdtemp())
        web = shared_playwright.start(folder)
        player = shared_playwright.start(folder)        # failed before: second driver on one thread
        self.assertIs(web._driver, player._driver)
        self.assertTrue(hasattr(player, 'chromium'))
        web.stop()
        self.assertIn(player._driver, [entry[0] for entry in shared_playwright._DRIVERS.values()])
        player.stop()
        self.assertNotIn(threading.get_ident(), shared_playwright._DRIVERS)
        player.stop()                                    # a second stop does nothing
        again = shared_playwright.start(folder)          # a fresh driver after the last one stopped
        again.stop()

    @on_fresh_thread
    def test_media_player_opens_a_file_while_the_web_browser_is_open(self):
        from media_player import MediaPlayer
        folder = Path(tempfile.mkdtemp())
        lease = shared_playwright.start(folder)          # stands in for an open web page's driver
        try:
            player = MediaPlayer(folder / 'player')
            try:
                player.start()                           # raised the asyncio-loop error before
            except Exception as error:
                self.assertNotIn('asyncio loop', str(error))
            finally:
                player.close()
        finally:
            lease.stop()


if __name__ == '__main__':
    unittest.main()

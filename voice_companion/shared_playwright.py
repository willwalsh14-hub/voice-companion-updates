"""One Playwright driver for the whole app thread.

Playwright's sync API allows only one driver per thread: a second
sync_playwright().start() fails with "using Playwright Sync API inside the asyncio
loop". The media player, the guided browser, embedded Edge and the regular-Chrome
link all run on the main thread, so opening a downloaded song while a web page was
open failed (Will, October 2026). Each user now borrows the same driver, and the
driver stops when the last one lets go."""
import threading
from pathlib import Path

_LOCK = threading.Lock()
_DRIVERS = {}   # thread id -> [driver, users]


class Lease:
    """Behaves like the Playwright object; stop() returns it instead of stopping it."""
    def __init__(self, driver, owner):
        self._driver, self._owner, self._stopped = driver, owner, False

    def __getattr__(self, name):
        return getattr(self._driver, name)

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        driver = None
        with _LOCK:
            entry = _DRIVERS.get(self._owner)
            if entry is not None and entry[0] is self._driver:
                entry[1] -= 1
                if entry[1] <= 0:
                    driver = _DRIVERS.pop(self._owner)[0]
        if driver is not None:
            driver.stop()


def bundled_browsers():
    """The browsers that ship with the app (Firefox for the audio player), or None."""
    import sys
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'browser-runtime'
    return root if root.is_dir() else None


def start(folder=None):
    """A driver for this thread, started once (inside the browser process
    environment, so Chrome and Firefox never load the app's own DLLs).

    The driver finds browsers through PLAYWRIGHT_BROWSERS_PATH, read once when it starts.
    Since the driver is shared, the first user decides it for all: when the web browser
    started it, the audio player's Firefox could not be found, and no podcast, radio
    station or file would play (Will, October 29, 2026). So it is always set here."""
    owner = threading.get_ident()
    with _LOCK:
        entry = _DRIVERS.get(owner)
        if entry is not None:
            entry[1] += 1
            return Lease(entry[0], owner)
    import os
    from playwright.sync_api import sync_playwright
    from browser_process import browser_process_environment
    bundled = bundled_browsers()
    if bundled is not None:
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(bundled)
    target = Path(folder) if folder is not None else Path.cwd()
    target.mkdir(parents=True, exist_ok=True)
    with browser_process_environment(target):
        driver = sync_playwright().start()
    with _LOCK:
        _DRIVERS[owner] = [driver, 1]
    return Lease(driver, owner)

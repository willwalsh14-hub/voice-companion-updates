"""Guided Chrome's window, kept out of the way for "Run the browser in the background".

Options, Web browser, "Guided Chrome window". Until 0.2.178 the background choice ran
Chrome headless. Google treats headless Chrome as a robot: searches went to Google's
"unusual traffic" page and YouTube said "Something went wrong" (Will, October 29,
2026). Now the background choice runs the same Chrome as "Show the browser window",
with a real window that is kept off the screen, minimized, off the taskbar and out of
Alt+Tab. Websites see an ordinary Chrome; nothing about Chrome's name is changed.

How it hides: the same way as Thunderbird's window (window_hiding.py): hide the
window, give it the tool-window style, show it again minimized and inactive. Only the
Chrome that Voice Companion started is touched, never the user's own Chrome: its
browser process is the one that appeared, under Voice Companion's own Playwright
driver, while guided Chrome started. The keyboard goes back to the window that had
it before Chrome started.

Everything that touches Windows goes through a desktop object, so tests use fakes.
"""
import os
import threading

PROGRAM = 'chrome.exe'
CHROME_CLASS = 'Chrome_WidgetWin_1'
POLL_SECONDS = 0.5
# Chrome's first window opens here, off every screen, until it is hidden.
OFF_SCREEN = '--window-position=-32000,-32000'


def process_table():
    """{pid: (parent pid, program name in lower case)} for every process, from Windows' snapshot."""
    import ctypes
    from ctypes import wintypes as w

    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD),
                    ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', w.DWORD),
                    ('szExeFile', w.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == w.HANDLE(-1).value:
        raise OSError('Windows did not list the running programs.')
    table = {}
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(Entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            table[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile.lower())
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return table


def started_browsers(table, root=None):
    """Chrome browser processes started under this program (by its Playwright driver).
    A browser process is a chrome.exe whose parent is not chrome.exe; Chrome's own
    helper processes are children of it."""
    root = os.getpid() if root is None else root
    found = set()
    for pid, (parent, name) in table.items():
        if name != PROGRAM or table.get(parent, (0, ''))[1] == PROGRAM:
            continue
        seen, current = set(), parent
        while current and current not in seen:
            if current == root:
                found.add(pid); break
            seen.add(current)
            current = table.get(current, (0, ''))[0]
    return found


class GuidedChromeWindow:
    """Keeps the windows of the guided Chrome started between before_launch() and
    after_launch() minimized and off the taskbar, until stop()."""

    def __init__(self, desktop=None, processes=process_table, root=None):
        if desktop is None:
            from window_hiding import Win32Desktop
            desktop = Win32Desktop(PROGRAM)
        self.desktop, self.processes, self.root = desktop, processes, root
        self.before = set(); self.pids = set(); self.previous = None
        self.tucked = set()
        self.stopped = threading.Event(); self.thread = None
        self.lock = threading.Lock()

    def before_launch(self):
        try:
            self.before = started_browsers(self.processes(), self.root)
        except OSError:
            self.before = set()
        try:
            self.previous = self.desktop.foreground()
        except Exception:
            self.previous = None

    def after_launch(self):
        """Hide what guided Chrome opened, give the keyboard back, and keep watching."""
        try:
            self.pids = started_browsers(self.processes(), self.root) - self.before
        except OSError:
            self.pids = set()
        self.poll()
        self._give_back_keyboard()
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='Guided Chrome window', daemon=True)
            self.thread.start()
        return bool(self.pids)

    def _give_back_keyboard(self):
        if not self.previous:
            return
        try:
            if self.desktop.foreground() == self.previous:
                return
            self.desktop.bring_forward(self.previous)
        except Exception:
            pass

    def poll(self):
        if not self.pids:
            return 0
        count = 0
        with self.lock:
            for window in self.desktop.windows(self.pids):
                # A new window (a pop-up) is hidden too; one shown again by Chrome is hidden again.
                if window.tool and window.minimized:
                    continue
                try:
                    self.desktop.tuck(window.handle); self.tucked.add(window.handle); count += 1
                except OSError:
                    pass
        return count

    def _run(self):
        while not self.stopped.wait(POLL_SECONDS):
            try:
                self.poll()
            except Exception:
                pass    # never stop watching: the next look tries again

    def stop(self):
        self.stopped.set()

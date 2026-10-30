"""Thunderbird's window, kept off the taskbar while Voice Companion uses it in the background.

Options, Email: "Thunderbird window".
  - "Minimized, as before" (the default): nothing here runs. Thunderbird is
    started minimized and asked to minimize its windows, exactly as before.
  - "Hidden from the taskbar": as above, and then Thunderbird's main window is
    also taken off the taskbar and out of Alt+Tab while it is minimized.

How it hides. Windows leaves a window off the taskbar and out of Alt+Tab when it
has the tool-window style (WS_EX_TOOLWINDOW). Microsoft's documented way to
change that on an existing window is: hide it, change the style, show it again.
The window is shown again minimized and inactive, so Thunderbird keeps running
(its add-on is Voice Companion's mail connection) and still knows it is a
minimized window: anything Thunderbird does to bring itself up (a sign-in, a
question, the user starting Thunderbird again) restores it as usual. Undoing it
puts the original style back.

Hiding the window instead (SW_HIDE) was not chosen: Thunderbird would still
believe its window is showing, so its own ways of coming forward would do
nothing, and the window could not be found at all.

What always wins over hiding (Will, October 2026):
  - While an account is being added or signed in to (busy()), nothing is hidden
    or restyled: every window is as it was before this option existed.
  - Every other Thunderbird window that comes up on screen, of any kind, is a
    window that may need the user. Hidden windows get their taskbar place back
    at once, the new window is brought to the front with the keyboard, and
    Voice Companion says so. If unsure, it shows.
  - Only the main windows found when hiding started are ever hidden; a window
    Thunderbird opens later is never hidden.
  - Hiding starts again only when no other Thunderbird window is open, nothing
    is being signed in to, and the main window is minimized again.
  - On exit, and when the option is changed back, every hidden window gets its
    taskbar place back. A window left hidden by a crash is put back the next
    time Voice Companion starts (restore_left_hidden).

Everything that touches Windows goes through a desktop object (Win32Desktop),
so tests use a fake that follows the same rules.
"""
import os
import queue
import threading
import time
from collections import namedtuple

MINIMIZED = 'Minimized, as before'
HIDDEN = 'Hidden from the taskbar'
CHOICES = (MINIMIZED, HIDDEN)
PREFERENCE = 'thunderbird_window'

PROGRAM = 'thunderbird.exe'
MAIN_CLASS = 'MozillaWindowClass'     # Thunderbird's main and writing windows
DIALOG_CLASS = 'MozillaDialogClass'   # its questions, sign-in and password windows
QUESTIONS = (DIALOG_CLASS, '#32770')   # #32770: a Windows dialog, such as choosing a file
POLL_SECONDS = 0.5
# A new main-type window must stay on screen this long before it counts: Thunderbird's
# own writing window, opened by the add-on to send, is minimized as soon as it loads.
# Question windows (QUESTIONS) count at once.
SETTLE_SECONDS = 3.0
# After a window needed the user: wait this long, with nothing open, before hiding again.
QUIET_SECONDS = 3.0

BACK = 'When you are done, press Alt+Tab to come back to Voice Companion.'
SIGN_IN = 'Thunderbird needs you to sign in. Its window is open now. ' + BACK
PASSWORD = 'Thunderbird is asking for a password. Its window is open now. ' + BACK
CERTIFICATE = 'Thunderbird is asking about a security certificate. Its window is open now. ' + BACK
UNSENT = 'Thunderbird is asking about messages that were not sent. Its window is open now. ' + BACK
UPDATE = 'Thunderbird has a message about an update. Its window is open now. ' + BACK
OTHER = 'Thunderbird opened a window that may need you{title}. It is open now. ' + BACK
MAIN_UP = 'Thunderbird brought its window up. It may be asking you something. Its window is open now. ' + BACK
NOT_IN_FRONT = ' If you do not hear it, press Alt+Tab to find it.'
SHOWN = 'Thunderbird is in front now. Press Alt+Tab to come back to Voice Companion. To put it away again, say hide Thunderbird.'
SHOWN_NOT_FRONT = 'Thunderbird is open, but it could not come to the front. Press Alt+Tab to find it.'
NOT_RUNNING = 'Thunderbird is not open right now.'
HIDDEN_NOW = 'Thunderbird is out of the way now. It keeps running for your email. Say show Thunderbird to see it.'
MINIMIZED_NOW = 'Thunderbird is minimized now. Say show Thunderbird to see it.'
STILL_NEEDED = ('Thunderbird still has a window open that may need you, so it stays where it is. '
                'Finish in that window, or close it, then say hide Thunderbird again.')
SIGNING_IN = 'Thunderbird stays in view while you sign in. It goes back out of the way when sign-in is done.'

Window = namedtuple('Window', 'handle kind title minimized owned tool')


def attention_message(title, in_front=True):
    """What to say when a Thunderbird window comes up: what it is, and how to get back."""
    words = ' '.join(str(title or '').split()).lower()
    if any(part in words for part in ('password',)):
        message = PASSWORD
    elif any(part in words for part in ('sign in', 'sign-in', 'signin', 'log in', 'login', 'authoriz', 'authentic',
                                        'verify', 'verification', 'two-step', '2-step', 'accounts.google', 'microsoft',
                                        'yahoo', 'oauth', 'account setup', 'set up your')):
        message = SIGN_IN
    elif 'certificate' in words or 'security exception' in words:
        message = CERTIFICATE
    elif 'unsent' in words or 'not sent' in words:
        message = UNSENT
    elif 'update' in words:
        message = UPDATE
    else:
        name = ' '.join(str(title or '').split())
        name = name if 0 < len(name) <= 80 else ''
        message = OTHER.format(title=': ' + name if name else '')
    return message + ('' if in_front else NOT_IN_FRONT)


class Win32Desktop:
    """The only part that calls Windows. Every call is safe from any thread."""
    GWL_STYLE, GWL_EXSTYLE, GW_OWNER = -16, -20, 4
    WS_CAPTION, WS_POPUP, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00C00000, 0x80000000, 0x00000080, 0x00040000
    SW_HIDE, SW_SHOWNA, SW_SHOWMINNOACTIVE, SW_RESTORE = 0, 8, 7, 9

    def __init__(self, program=PROGRAM):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        self.program = program.lower()
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user, self.kernel = user, kernel
        long_ptr = ctypes.c_ssize_t
        self.ENUM = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        for name, result, arguments in (
                ('EnumWindows', w.BOOL, [self.ENUM, w.LPARAM]), ('IsWindow', w.BOOL, [w.HWND]),
                ('IsWindowVisible', w.BOOL, [w.HWND]), ('IsIconic', w.BOOL, [w.HWND]),
                ('GetClassNameW', ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]),
                ('GetWindowTextW', ctypes.c_int, [w.HWND, w.LPWSTR, ctypes.c_int]),
                ('GetWindowTextLengthW', ctypes.c_int, [w.HWND]),
                ('GetWindowThreadProcessId', w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
                ('GetWindow', w.HWND, [w.HWND, w.UINT]), ('ShowWindow', w.BOOL, [w.HWND, ctypes.c_int]),
                ('GetWindowLongPtrW', long_ptr, [w.HWND, ctypes.c_int]),
                ('SetWindowLongPtrW', long_ptr, [w.HWND, ctypes.c_int, long_ptr]),
                ('GetForegroundWindow', w.HWND, []), ('SetForegroundWindow', w.BOOL, [w.HWND]),
                ('BringWindowToTop', w.BOOL, [w.HWND]), ('AttachThreadInput', w.BOOL, [w.DWORD, w.DWORD, w.BOOL]),
                ('SetWindowPos', w.BOOL, [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]),
                ('GetWindowRect', w.BOOL, [w.HWND, ctypes.POINTER(w.RECT)])):
            function = getattr(user, name); function.restype = result; function.argtypes = arguments
        kernel.OpenProcess.restype = w.HANDLE; kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.QueryFullProcessImageNameW.restype = w.BOOL
        kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.GetCurrentThreadId.restype = w.DWORD
        self._names = {}

    def _process(self, handle):
        pid = self.w.DWORD()
        thread = self.user.GetWindowThreadProcessId(handle, self.ctypes.byref(pid))
        return pid.value, thread

    def _program(self, pid):
        if pid not in self._names:
            name = ''
            process = self.kernel.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if process:
                try:
                    size = self.w.DWORD(32768)
                    text = self.ctypes.create_unicode_buffer(size.value)
                    if self.kernel.QueryFullProcessImageNameW(process, 0, text, self.ctypes.byref(size)):
                        name = os.path.basename(text.value).lower()
                finally:
                    self.kernel.CloseHandle(process)
            if len(self._names) > 500: self._names.clear()
            self._names[pid] = name
        return self._names[pid]

    def _text(self, function, handle, size):
        text = self.ctypes.create_unicode_buffer(size + 1)
        function(handle, text, size + 1)
        return text.value

    def windows(self, pids=None):
        """Thunderbird's own windows a person could see: visible and top level, with a title bar
        or at least not a pop-up (menus and tooltips are pop-ups without one).
        pids, when given: only windows of these processes (guided Chrome, not the user's Chrome)."""
        handles = []
        self.user.EnumWindows(self.ENUM(lambda handle, _: handles.append(handle) or True), 0)
        found = []
        for handle in handles:
            try:
                if not self.user.IsWindowVisible(handle): continue
                style = self.user.GetWindowLongPtrW(handle, self.GWL_STYLE) & 0xFFFFFFFF
                if style & self.WS_CAPTION != self.WS_CAPTION and style & self.WS_POPUP: continue
                pid, _ = self._process(handle)
                if pids is not None and pid not in pids: continue
                if self._program(pid) != self.program: continue
                kind = self._text(self.user.GetClassNameW, handle, 256)
                title = self._text(self.user.GetWindowTextW, handle, min(self.user.GetWindowTextLengthW(handle), 1000))
                extended = self.user.GetWindowLongPtrW(handle, self.GWL_EXSTYLE)
                found.append(Window(int(handle), kind, title, bool(self.user.IsIconic(handle)),
                                    bool(self.user.GetWindow(handle, self.GW_OWNER)), bool(extended & self.WS_EX_TOOLWINDOW)))
            except OSError:
                continue
        return found

    def exists(self, handle):
        return bool(self.user.IsWindow(handle))

    def style(self, handle):
        return int(self.user.GetWindowLongPtrW(handle, self.GWL_EXSTYLE))

    def tuck(self, handle):
        """Off the taskbar and out of Alt+Tab, still minimized. Returns the style to put back."""
        before = self.style(handle)
        self.user.ShowWindow(handle, self.SW_HIDE)
        self.user.SetWindowLongPtrW(handle, self.GWL_EXSTYLE, (before | self.WS_EX_TOOLWINDOW) & ~self.WS_EX_APPWINDOW)
        self.user.ShowWindow(handle, self.SW_SHOWMINNOACTIVE)
        return before

    OFF_SCREEN = -32000

    def park(self, handle):
        """Off the taskbar and out of Alt+Tab, and off every screen, but not minimized: a
        minimized window stops drawing, and a video in it stops (guided Chrome, October 30, 2026).
        Returns the style to put back."""
        before = self.style(handle)
        self.user.ShowWindow(handle, self.SW_HIDE)
        self.user.SetWindowLongPtrW(handle, self.GWL_EXSTYLE, (before | self.WS_EX_TOOLWINDOW) & ~self.WS_EX_APPWINDOW)
        if self.user.IsIconic(handle):
            self.user.ShowWindow(handle, 4)   # SW_SHOWNOACTIVATE: restored, without the keyboard
        self.user.SetWindowPos(handle, None, self.OFF_SCREEN, self.OFF_SCREEN, 0, 0, 0x0001 | 0x0004 | 0x0010)  # no size, z-order or activation
        self.user.ShowWindow(handle, self.SW_SHOWNA)
        return before

    def off_screen(self, handle):
        rect = self.w.RECT()
        if not self.user.GetWindowRect(handle, self.ctypes.byref(rect)):
            return False
        return rect.right <= -10000 or rect.bottom <= -10000

    def untuck(self, handle, style=None):
        """The window's own style again, so it is back on the taskbar and in Alt+Tab."""
        if not self.user.IsWindow(handle): return
        current = self.style(handle)
        original = style if style is not None else current & ~self.WS_EX_TOOLWINDOW
        minimized = bool(self.user.IsIconic(handle))
        self.user.ShowWindow(handle, self.SW_HIDE)
        self.user.SetWindowLongPtrW(handle, self.GWL_EXSTYLE, original)
        self.user.ShowWindow(handle, self.SW_SHOWMINNOACTIVE if minimized else self.SW_SHOWNA)

    def minimize(self, handle):
        self.user.ShowWindow(handle, self.SW_SHOWMINNOACTIVE)

    def bring_forward(self, handle):
        """Restore the window and give it the keyboard, as app_window.foreground_window does.
        True when Windows reports it as the foreground window."""
        if self.user.IsIconic(handle): self.user.ShowWindow(handle, self.SW_RESTORE)
        current = self.kernel.GetCurrentThreadId()
        _, other = self._process(self.user.GetForegroundWindow())
        attached = False
        try:
            if other and current != other: attached = bool(self.user.AttachThreadInput(current, other, True))
            self.user.BringWindowToTop(handle); self.user.SetForegroundWindow(handle)
        finally:
            if attached: self.user.AttachThreadInput(current, other, False)
        return int(self.user.GetForegroundWindow() or 0) == int(handle)

    def foreground(self):
        return int(self.user.GetForegroundWindow() or 0)

    def own_foreground(self):
        pid, _ = self._process(self.user.GetForegroundWindow())
        return pid == os.getpid()


class ThunderbirdWindows:
    """Hides Thunderbird's main window while it is minimized; shows anything else it opens.

    poll() does the work; a thread calls it every half second (start()). Spoken
    messages wait in .messages for the main loop."""

    def __init__(self, desktop, busy=lambda: False, clock=time.monotonic):
        self.desktop, self.busy, self.clock = desktop, busy, clock
        self.hiding = False          # the option is on and Voice Companion started Thunderbird
        self.tucked = {}             # handle -> its own style, for the windows hidden now
        self.mains = set()           # the main windows found when hiding started: the only ones ever hidden
        self.waiting = 'none'        # 'none', 'attention' (a window needed the user), 'shown' (asked to show)
        self.seen = {}               # handle -> when it was first seen on screen
        self.announced = set()       # windows already brought forward and said while signing in
        self.quiet_since = None
        self.messages = queue.Queue()
        self.lock = threading.RLock()
        self.thread = None
        self.stopped = threading.Event()

    # ---- turning it on and off ----------------------------------------------------

    def enable(self):
        """Start hiding: the main windows Thunderbird has now are the ones hidden while minimized."""
        with self.lock:
            self.hiding = True
            windows = self.desktop.windows()
            self.mains = {w.handle for w in windows if w.kind == MAIN_CLASS and not w.owned}
            # Already on screen (shown on purpose): hidden only once it is minimized again.
            self.waiting = 'shown' if any(not w.minimized for w in windows) else 'none'
            self.seen.clear(); self.quiet_since = None
            self.poll()

    def disable(self):
        """Back to "Minimized, as before": every hidden window gets its taskbar place back."""
        with self.lock:
            self.hiding = False
            self.release()
            self.mains.clear(); self.waiting = 'none'; self.seen.clear()

    def release(self):
        """Put every hidden window back as it was (still minimized)."""
        with self.lock:
            for handle, style in list(self.tucked.items()):
                try: self.desktop.untuck(handle, style)
                except OSError: pass
                self.tucked.pop(handle, None)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='Thunderbird window', daemon=True)
            self.thread.start()
        return self

    def stop(self):
        self.stopped.set()
        self.disable()

    def _run(self):
        while not self.stopped.wait(POLL_SECONDS):
            try:
                self.poll()
            except Exception:
                pass    # never stop watching: the next look tries again

    # ---- watching -----------------------------------------------------------------

    def poll(self):
        with self.lock:
            if not self.hiding:
                return
            windows = self.desktop.windows()
            present = {w.handle for w in windows}
            for handle in list(self.tucked):
                if handle not in present and not self.desktop.exists(handle): self.tucked.pop(handle, None)
            self.mains &= present | set(self.tucked)
            for handle in list(self.seen):
                if handle not in present: self.seen.pop(handle)
            self.announced &= present
            now = self.clock()
            if self.busy():
                # Signing in or adding an account: nothing is hidden. A window Thunderbird opens for
                # it (Microsoft's sign-in, a password box, a second check) is still brought to the
                # front and said, once: it opened silently behind Voice Companion before
                # (Will, October 30, 2026).
                self.release(); self.quiet_since = None
                if self.waiting == 'none': self.waiting = 'attention'
                for window in windows:
                    if window.minimized or window.handle in self.mains or window.handle in self.announced:
                        continue
                    first = self.seen.setdefault(window.handle, now)
                    if window.kind in QUESTIONS or now - first >= SETTLE_SECONDS:
                        self.announced.add(window.handle)
                        try:
                            in_front = self.desktop.bring_forward(window.handle)
                        except OSError:
                            in_front = False
                        self.messages.put(attention_message(window.title, in_front))
                        break
                return
            # A window on screen that Voice Companion did not put there needs the user.
            showing = [w for w in windows if not w.minimized]
            for window in showing:
                self.seen.setdefault(window.handle, now)
            if self.waiting == 'none':
                # Questions and a hidden main window coming up count at once; any other
                # window once it has stayed on screen (it may be a writing window about to minimize).
                settled = [w for w in showing if w.kind in QUESTIONS or w.handle in self.mains
                           or now - self.seen[w.handle] >= SETTLE_SECONDS]
                if settled:
                    self._attention(sorted(settled, key=lambda w: (w.kind not in QUESTIONS, w.handle in self.mains))[0])
                    return
                for window in windows:
                    if window.handle in self.mains and window.minimized and window.handle not in self.tucked:
                        self.tucked[window.handle] = self.desktop.tuck(window.handle)
                return
            # Waiting for the user: hide again only when nothing else is open, the main
            # window is minimized again, and that has stayed so for a few seconds.
            others = [w for w in windows if w.handle not in self.mains]
            if showing or others:
                self.quiet_since = None
                return
            if self.quiet_since is None:
                self.quiet_since = now
                return
            if now - self.quiet_since >= QUIET_SECONDS:
                self.waiting = 'none'; self.quiet_since = None
                for window in windows:
                    if window.handle in self.mains and window.handle not in self.tucked:
                        self.tucked[window.handle] = self.desktop.tuck(window.handle)

    def _attention(self, window):
        self.release()
        self.waiting = 'attention'; self.quiet_since = None
        try:
            in_front = self.desktop.bring_forward(window.handle)
        except OSError:
            in_front = False
        if window.handle in self.mains and window.kind not in QUESTIONS:
            self.messages.put(MAIN_UP + ('' if in_front else NOT_IN_FRONT))
        else:
            self.messages.put(attention_message(window.title, in_front))

    # ---- what the user asks for ---------------------------------------------------

    def show(self):
        """ "Show Thunderbird": its taskbar place back, its main window restored and in front.
        Returns what to say."""
        with self.lock:
            windows = self.desktop.windows()
            if not windows:
                return NOT_RUNNING
            self.release()
            if self.hiding: self.waiting = 'shown'; self.quiet_since = None
            target = next((w for w in windows if w.kind in QUESTIONS), None) or \
                next((w for w in windows if w.handle in self.mains), None) or \
                next((w for w in windows if w.kind == MAIN_CLASS and not w.owned), windows[0])
            try:
                in_front = self.desktop.bring_forward(target.handle)
            except OSError:
                in_front = False
            return SHOWN if in_front else SHOWN_NOT_FRONT

    def hide(self, started_by_companion=True):
        """ "Hide Thunderbird": minimize its main window, and with the option on, hide it.
        A window that may need the user, or a sign-in, keeps it where it is."""
        with self.lock:
            windows = self.desktop.windows()
            if not windows:
                return NOT_RUNNING
            if self.busy():
                return SIGNING_IN
            mains = self.mains or {w.handle for w in windows if w.kind == MAIN_CLASS and not w.owned}
            if any(w for w in windows if w.handle not in mains and not w.minimized):
                return STILL_NEEDED
            for window in windows:
                if window.handle in mains and not window.minimized:
                    self.desktop.minimize(window.handle)
            if not (self.hiding and started_by_companion):
                return MINIMIZED_NOW
            self.waiting = 'none'; self.seen.clear(); self.quiet_since = None
            self.mains = set(mains)
            for window in windows:
                if window.handle in mains and window.handle not in self.tucked:
                    self.tucked[window.handle] = self.desktop.tuck(window.handle)
            return HIDDEN_NOW

    def take_message(self):
        try:
            return self.messages.get_nowait()
        except queue.Empty:
            return None


def restore_left_hidden(desktop):
    """Thunderbird windows still hidden from an earlier run that ended suddenly get their
    taskbar place back. Thunderbird never gives its own title-bar windows the tool style."""
    count = 0
    for window in desktop.windows():
        if window.tool and window.kind == MAIN_CLASS and not window.owned:
            try:
                desktop.untuck(window.handle); count += 1
            except OSError:
                pass
    return count

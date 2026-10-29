"""Thunderbird off the taskbar, on real Windows (window_hiding.Win32Desktop).

Will, October 2026: Thunderbird may be kept off the taskbar, but every window it
opens, above all a sign-in, must come to the front with the keyboard. These
checks use real windows: a stand-in program named thunderbird.exe makes windows
with Thunderbird's own window classes. With VOICE_COMPANION_REAL_THUNDERBIRD set
to thunderbird.exe, the same is checked with the real Thunderbird.
Windows only; they run on a try branch before joining a release.
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import window_hiding as hiding

STAND_IN = r'''
"""A stand-in for Thunderbird's windows, for the Windows checks in test_window_hiding_windows.py.

It makes real Windows windows with Thunderbird's window classes: a main window
(MozillaWindowClass), started minimized and inactive as Voice Companion starts
Thunderbird. Commands arrive one per line on standard input:
  dialog <title>   open a sign-in or question window (MozillaDialogClass), owned by the main window
  close-dialog     close it
  compose          open a writing window and minimize it a moment later, as the add-on does
  restore          bring the main window up, as Thunderbird does when it wants the user
  quit             end
Each command answers "ok <window handle>" on standard output. Not shipped to users' screens:
it runs only in tests.
"""
import ctypes
import queue
import sys
import threading
import time
from ctypes import wintypes as w

user = ctypes.WinDLL('user32', use_last_error=True)
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
user.DefWindowProcW.restype = ctypes.c_ssize_t
user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user.CreateWindowExW.restype = w.HWND
user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user.DestroyWindow.argtypes = [w.HWND]
user.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
user.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', w.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', w.HINSTANCE), ('hIcon', w.HICON), ('hCursor', w.HANDLE), ('hbrBackground', w.HBRUSH),
                ('lpszMenuName', w.LPCWSTR), ('lpszClassName', w.LPCWSTR)]


procedure = WNDPROC(lambda hwnd, message, wparam, lparam: user.DefWindowProcW(hwnd, message, wparam, lparam))
instance = kernel.GetModuleHandleW(None)
for name in ('MozillaWindowClass', 'MozillaDialogClass'):
    entry = WNDCLASSW(); entry.lpfnWndProc = procedure; entry.hInstance = instance; entry.lpszClassName = name
    entry.hbrBackground = 6   # COLOR_WINDOW + 1
    user.RegisterClassW(ctypes.byref(entry))

OVERLAPPED = 0x00CF0000          # WS_OVERLAPPEDWINDOW
DIALOG = 0x00C80000 | 0x00080000  # WS_CAPTION | WS_SYSMENU (WS_POPUP-less, as Gecko's dialogs)


def window(kind, title, style, owner=None):
    return user.CreateWindowExW(0, kind, title, style, 100, 100, 640, 480, owner, None, instance, None)


def main():
    commands = queue.Queue()
    threading.Thread(target=lambda: [commands.put(line.strip()) for line in sys.stdin], daemon=True).start()
    main_window = window('MozillaWindowClass', 'Inbox - Mozilla Thunderbird', OVERLAPPED)
    user.ShowWindow(main_window, 7)                     # SW_SHOWMINNOACTIVE, as launch_background does
    dialog = None
    later = []
    print('ok', int(main_window), flush=True)
    message = w.MSG()
    while True:
        while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            user.TranslateMessage(ctypes.byref(message)); user.DispatchMessageW(ctypes.byref(message))
        for when, action in list(later):
            if time.monotonic() >= when:
                later.remove((when, action)); action()
        try:
            command = commands.get_nowait()
        except queue.Empty:
            time.sleep(0.01); continue
        if command.startswith('dialog '):
            dialog = window('MozillaDialogClass', command[7:], DIALOG, main_window)
            user.ShowWindow(dialog, 5)                  # SW_SHOW: Thunderbird shows its own windows
            print('ok', int(dialog), flush=True)
        elif command == 'close-dialog':
            if dialog: user.DestroyWindow(dialog)
            dialog = None
            print('ok 0', flush=True)
        elif command == 'compose':
            compose = window('MozillaWindowClass', 'Write: (no subject) - Thunderbird', OVERLAPPED)
            user.ShowWindow(compose, 4)                 # SW_SHOWNOACTIVATE, then minimized by the add-on
            later.append((time.monotonic() + 0.3, lambda c=compose: user.ShowWindow(c, 7)))
            print('ok', int(compose), flush=True)
        elif command == 'restore':
            user.ShowWindow(main_window, 9)             # SW_RESTORE
            print('ok', int(main_window), flush=True)
        elif command == 'quit':
            print('ok 0', flush=True)
            return


if __name__ == '__main__':
    main()
'''


def taskbar_names():
    """The names of the taskbar's buttons, read with UI Automation, or None when it cannot be read."""
    script = ("Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes;"
              "$root=[Windows.Automation.AutomationElement]::RootElement;"
              "$c=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ClassNameProperty,'Shell_TrayWnd');"
              "$tray=$root.FindFirst([Windows.Automation.TreeScope]::Children,$c);"
              "if($tray){$tray.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)|%{$_.Current.Name}}")
    try:
        found = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True,
                               text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = [line.strip() for line in found.splitlines() if line.strip()]
    return names or None


def on_taskbar(title):
    names = taskbar_names()
    return None if names is None else any(title in name for name in names)


@unittest.skipUnless(os.name == 'nt', 'Real Windows windows')
class StandInThunderbirdTests(unittest.TestCase):
    TITLE = 'Inbox - Mozilla Thunderbird'

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp(prefix='vc-stand-in-')
        cls.script = os.path.join(cls.folder, 'stand_in.py')
        with open(cls.script, 'w', encoding='utf-8') as handle: handle.write(STAND_IN)
        # A copy of Python named thunderbird.exe, next to Python's own files so it starts.
        # Copy the real Python, not a virtual environment's python.exe: that one is only a
        # launcher that starts the real Python as a separate program, so the windows would
        # belong to python.exe, not thunderbird.exe (the Windows build uses a virtual environment).
        real = getattr(sys, '_base_executable', None) or sys.executable
        cls.program = os.path.join(os.path.dirname(real), 'thunderbird.exe')
        shutil.copyfile(real, cls.program)

    @classmethod
    def tearDownClass(cls):
        for path in (cls.program,):
            try: os.remove(path)
            except OSError: pass
        shutil.rmtree(cls.folder, ignore_errors=True)

    def setUp(self):
        self.process = subprocess.Popen([self.program, self.script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.main = int(self.process.stdout.readline().split()[1])
        self.desktop = hiding.Win32Desktop()
        self.signing_in = False
        self.watch = hiding.ThunderbirdWindows(self.desktop, busy=lambda: self.signing_in)

    def tearDown(self):
        self.watch.disable()
        try:
            self.ask('quit'); self.process.wait(10)
        except Exception:
            self.process.kill()

    def ask(self, command):
        self.process.stdin.write(command + '\n'); self.process.stdin.flush()
        return int(self.process.stdout.readline().split()[1])

    def style(self, handle):
        return self.desktop.style(handle)

    def poll_for(self, seconds, until=lambda: False):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.watch.poll()
            if until(): return True
            time.sleep(0.25)
        return until()

    def test_main_window_is_found_minimized(self):
        found = {w.handle: w for w in self.desktop.windows()}
        self.assertIn(self.main, found, found)
        self.assertEqual(found[self.main].kind, hiding.MAIN_CLASS)
        self.assertTrue(found[self.main].minimized)

    def test_hidden_main_window_leaves_the_taskbar_and_comes_back(self):
        before = on_taskbar(self.TITLE)
        self.watch.enable()
        user = ctypes.windll.user32
        self.assertTrue(self.style(self.main) & 0x80, 'tool-window style set')
        self.assertTrue(user.IsWindowVisible(self.main)); self.assertTrue(user.IsIconic(self.main))
        time.sleep(1)
        hidden = on_taskbar(self.TITLE)
        self.watch.disable()
        self.assertFalse(self.style(self.main) & 0x80, 'tool-window style removed')
        time.sleep(1)
        after = on_taskbar(self.TITLE)
        print('TASKBAR before=%s hidden=%s after=%s' % (before, hidden, after), flush=True)
        print('TASKBAR names: %r' % (taskbar_names(),), flush=True)
        if before:
            self.assertFalse(hidden); self.assertTrue(after)

    def test_sign_in_window_comes_to_the_front_with_the_keyboard(self):
        self.watch.enable()
        dialog = self.ask('dialog Sign in - Google Accounts')
        self.assertTrue(self.poll_for(5, lambda: not self.watch.messages.empty()), 'the sign-in window was noticed')
        self.assertEqual(self.watch.take_message(), hiding.SIGN_IN)
        user = ctypes.windll.user32
        front = user.GetForegroundWindow()
        self.assertEqual(front, dialog, 'foreground is %r, sign-in is %r' % (front, dialog))
        self.assertFalse(self.style(self.main) & 0x80, 'the main window is back on the taskbar')
        self.ask('close-dialog')
        user.ShowWindow(self.main, 7)
        self.assertTrue(self.poll_for(8, lambda: bool(self.style(self.main) & 0x80)), 'hidden again once things are quiet')

    def test_signing_in_leaves_every_window_as_before(self):
        self.watch.enable()
        self.signing_in = True
        self.poll_for(1)
        self.assertFalse(self.style(self.main) & 0x80)
        dialog = self.ask('dialog Verify it is you')
        self.poll_for(3)
        self.assertTrue(self.watch.messages.empty())
        self.assertFalse(self.style(dialog) & 0x80)

    def test_writing_window_is_not_announced_or_hidden(self):
        self.watch.enable()
        compose = self.ask('compose')
        self.poll_for(5)
        self.assertTrue(self.watch.messages.empty())
        self.assertFalse(self.style(compose) & 0x80)

    def test_thunderbird_bringing_itself_up_is_brought_forward(self):
        self.watch.enable()
        self.ask('restore')
        self.assertTrue(self.poll_for(6, lambda: not self.watch.messages.empty()))
        self.assertEqual(self.watch.take_message(), hiding.MAIN_UP)
        self.assertEqual(ctypes.windll.user32.GetForegroundWindow(), self.main)
        self.assertFalse(self.style(self.main) & 0x80)

    def test_window_left_hidden_is_put_back(self):
        self.desktop.tuck(self.main)
        self.assertTrue(self.style(self.main) & 0x80)
        self.assertEqual(hiding.restore_left_hidden(self.desktop), 1)
        self.assertFalse(self.style(self.main) & 0x80)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('VOICE_COMPANION_REAL_THUNDERBIRD'), 'Needs a real Thunderbird')
class RealThunderbirdTests(unittest.TestCase):
    """The real Thunderbird: started as Voice Companion starts it, hidden, and brought back up
    when it is started again (as a person does from the Start menu)."""

    def test_real_thunderbird(self):
        program = os.environ['VOICE_COMPANION_REAL_THUNDERBIRD']
        # Its own profile, without the first-start question about the default mail program,
        # started the way thunderbird_setup.WindowsSystem.launch_background starts it.
        profile = tempfile.mkdtemp(prefix='vc-thunderbird-')
        with open(os.path.join(profile, 'user.js'), 'w') as handle:
            handle.write('user_pref("mail.shell.checkDefaultClient", false);\n')
        info = subprocess.STARTUPINFO(); info.dwFlags |= subprocess.STARTF_USESHOWWINDOW; info.wShowWindow = 7
        subprocess.Popen([program, '-profile', profile], startupinfo=info, close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        desktop = hiding.Win32Desktop()
        watch = hiding.ThunderbirdWindows(desktop)
        try:
            deadline = time.monotonic() + 90
            windows = []
            while time.monotonic() < deadline and not windows:
                windows = desktop.windows(); time.sleep(1)
            print('REAL windows at start: %r' % (windows,), flush=True)
            self.assertTrue(windows, 'Thunderbird made no window')
            time.sleep(10)
            windows = desktop.windows()
            print('REAL windows after 10 s: %r' % (windows,), flush=True)
            # The add-on's "minimize" request, as Voice Companion sends it once Thunderbird is connected.
            for window in windows:
                if window.kind == hiding.MAIN_CLASS and not window.owned: desktop.minimize(window.handle)
            time.sleep(1)
            watch.enable()
            for _ in range(20):
                watch.poll(); time.sleep(0.25)
            print('REAL after enable: waiting=%s tucked=%r messages=%r' % (watch.waiting, watch.tucked, list(watch.messages.queue)), flush=True)
            tucked = dict(watch.tucked)
            self.assertTrue(tucked, 'a minimized main window was hidden')
            time.sleep(5)
            for handle in tucked:
                self.assertTrue(desktop.style(handle) & 0x80, 'Thunderbird kept the tool-window style')
            watch.waiting = 'none'; watch.seen.clear()
            while not watch.messages.empty(): watch.messages.get_nowait()
            def start_again(hidden):
                """Start Thunderbird again, as from the Start menu. True when its window came up."""
                subprocess.Popen([program, '-profile', profile])
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if hidden:
                        watch.poll()
                        if not watch.messages.empty(): return True
                    elif any(not w.minimized for w in desktop.windows()):
                        return True
                    time.sleep(0.25)
                return False
            hidden_came_up = start_again(True)
            print('REAL second start while hidden: came_up=%s windows=%r' % (hidden_came_up, desktop.windows()), flush=True)
            # "Show Thunderbird" brings it up with the keyboard, and back on the taskbar.
            said = watch.show()
            front = ctypes.windll.user32.GetForegroundWindow()
            print('REAL show: said=%r foreground=%r windows=%r' % (said, front, desktop.windows()), flush=True)
            self.assertEqual(said, hiding.SHOWN)
            for handle in tucked:
                self.assertFalse(desktop.style(handle) & 0x80, 'back on the taskbar')
            self.assertIn(front, tucked)
            # "Hide Thunderbird" puts it away again, and the style stays on Thunderbird's window.
            self.assertEqual(watch.hide(), hiding.HIDDEN_NOW)
            time.sleep(3)
            for handle in tucked:
                self.assertTrue(desktop.style(handle) & 0x80)
            # The same second start with only "Minimized, as before": hiding must not make it worse.
            watch.disable()
            plain_came_up = start_again(False)
            print('REAL second start while minimized as before: came_up=%s windows=%r' % (plain_came_up, desktop.windows()), flush=True)
            self.assertTrue(hidden_came_up or not plain_came_up,
                            'a second start brings Thunderbird up when minimized, but not when hidden')
        finally:
            watch.disable()
            subprocess.run(['taskkill', '/IM', 'thunderbird.exe', '/F'], capture_output=True)
            time.sleep(2); shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()

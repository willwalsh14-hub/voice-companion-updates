"""Windows: the audio player plays with no window and never takes the keyboard (tester, October 2026).

"I dislike how it pulls up another window ... having to tab over to it will create problems."
The real player starts its real bundled Firefox, as Companion does, and plays a
real file. The checks:
  - no window of the player's Firefox is visible on screen, at start or while playing;
  - the window that had the keyboard still has it;
  - the audio really plays (its place moves forward) without a window.
Whether Windows has a sound session for it is written to the test output, because
build computers may have no sound card.
"""
import io
import math
import os
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path


def tone(seconds=6, rate=16000):
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(rate)
        audio.writeframes(b''.join(struct.pack('<h', int(3000 * math.sin(2 * math.pi * 440 * n / rate)))
                                   for n in range(seconds * rate)))
    return data.getvalue()


@unittest.skipUnless(sys.platform == 'win32', 'Windows windows, focus and sound')
class PlayerWindowTests(unittest.TestCase):
    def setUp(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        bundled = Path(__file__).parent / 'browser-runtime'
        if bundled.exists() and not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(bundled)

    def processes(self):
        ctypes, w = self.ctypes, self.wintypes

        class Entry(ctypes.Structure):
            _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                        ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD),
                        ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', ctypes.c_long), ('dwFlags', w.DWORD),
                        ('szExeFile', ctypes.c_wchar * 260)]
        found = {}
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        try:
            entry = Entry();entry.dwSize = ctypes.sizeof(entry)
            more = self.kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                found[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile.lower())
                more = self.kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        return found

    def player_processes(self):
        """Firefox processes started (through Playwright's driver) by this test process."""
        table, own = self.processes(), os.getpid()
        mine = set()
        for pid in table:
            child, depth = pid, 0
            while depth < 12 and child in table:
                parent = table[child][0]
                if parent == own:
                    mine.add(pid);break
                if parent in (0, child):break
                child, depth = parent, depth + 1
        return {pid for pid in mine if table[pid][1] == 'firefox.exe'}

    def visible_windows(self, pids):
        found = []
        callback = self.ctypes.WINFUNCTYPE(self.wintypes.BOOL, self.wintypes.HWND, self.wintypes.LPARAM)

        def each(handle, _):
            pid = self.wintypes.DWORD()
            self.user.GetWindowThreadProcessId(handle, self.ctypes.byref(pid))
            if pid.value in pids and self.user.IsWindowVisible(handle):
                title = self.ctypes.create_unicode_buffer(200)
                self.user.GetWindowTextW(handle, title, 200)
                found.append((pid.value, title.value))
            return True
        self.user.EnumWindows(callback(each), 0)
        return found

    def test_playing_shows_no_window_keeps_the_keyboard_and_plays(self):
        import tkinter as tk
        from media_player import MediaPlayer
        audio = Path(self.temp.name) / 'tone.wav';audio.write_bytes(tone())
        root = tk.Tk()
        try:
            root.title('Voice Companion focus check');root.geometry('300x120+40+40')
            root.deiconify();root.update();root.focus_force();root.update()
            before = self.user.GetForegroundWindow()
            player = MediaPlayer(Path(self.temp.name) / 'Audio Player')
            try:
                windows_seen, pids = [], set()
                player.open(audio.as_uri(), 'Tone', 'podcast')
                start = player.status()['position']
                end = time.monotonic() + 2.5
                while time.monotonic() < end:
                    root.update()
                    pids |= self.player_processes()
                    windows_seen += self.visible_windows(pids)
                    time.sleep(.1)
                status = player.status()
                self.assertTrue(pids, 'The player started no Firefox under this process.')
                self.assertEqual(windows_seen, [], 'The player showed a window.')
                self.assertEqual(self.user.GetForegroundWindow(), before, 'The keyboard moved away from the window that had it.')
                self.assertFalse(status['paused'], getattr(player, 'last_start_error', ''))
                self.assertGreater(status['position'] - start, 1.0, 'The audio did not play forward.')
                self.assertEqual(player.volume, 0.5)
                self.report_sound_session(pids)
            finally:
                player.close()
        finally:
            root.destroy()

    def report_sound_session(self, pids):
        """Written to the output only: build computers may have no sound card."""
        try:
            from audio_ducking import WindowsAudioSessions, initialize_com
            initialize_com()
            sessions = [(s.pid, s.name) for s in WindowsAudioSessions().sessions()]
            ours = [s for s in sessions if s[0] in pids]
            print('\nSOUND SESSION CHECK: player sessions ' + repr(ours) + '; all sessions ' + repr(sessions[:12]), file=sys.stderr)
        except Exception as exc:
            print('\nSOUND SESSION CHECK: Windows sound sessions could not be read: ' + repr(exc), file=sys.stderr)


if __name__ == '__main__':
    unittest.main()

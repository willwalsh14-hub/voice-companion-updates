"""Audio ducking against real Windows Core Audio (Windows only).

Checks the ctypes COM calls in WindowsAudioSessions: the process list, process
start times, and, when the PC has a playback device, that a real child process
playing sound is lowered and put back.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

from audio_ducking import AudioDucker, WindowsAudioSessions


@unittest.skipUnless(os.name == 'nt', 'Windows audio sessions')
class WindowsAudioSessionTests(unittest.TestCase):
    def test_process_list_and_start_times(self):
        windows = WindowsAudioSessions()
        processes = windows.processes()
        self.assertIn(os.getpid(), processes)
        parent, name = processes[os.getpid()]
        self.assertTrue(name.endswith('.exe'), name)
        self.assertEqual(parent, os.getppid())
        self.assertIsNotNone(windows.created(os.getpid()))
        if windows.created(parent) is not None:
            self.assertLessEqual(windows.created(parent), windows.created(os.getpid()))

    def test_session_list_does_not_fail(self):
        sessions = WindowsAudioSessions().sessions()
        for session in sessions:
            self.assertTrue(0.0 <= session.get_volume() <= 1.0)
            session.close()

    def test_child_playing_sound_is_lowered_and_put_back(self):
        folder = Path(tempfile.mkdtemp())
        sound = folder / 'tone.wav'
        with wave.open(str(sound), 'wb') as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000)
            out.writeframes(b'\x00\x10\x00\xf0' * 8000 * 20)
        child = subprocess.Popen([sys.executable, '-c',
                                  'import winsound,sys,time;winsound.PlaySound(sys.argv[1],winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_LOOP);time.sleep(60)',
                                  str(sound)])
        self.addCleanup(child.kill)
        windows = WindowsAudioSessions()
        session = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and session is None:
            session = next((s for s in windows.sessions() if s.pid == child.pid), None)
            time.sleep(0.2)
        if session is None:
            self.skipTest('This PC has no playback device, so no audio session was made.')
        before = session.get_volume()
        ducker = AudioDucker(WindowsAudioSessions, folder / 'audio-ducking.json', log=print).start()
        try:
            ducker.request(True, 0.25, False)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and abs(session.get_volume() - before * 0.25) > 0.01:
                ducker.request(True, 0.25, False);time.sleep(0.05)
            self.assertAlmostEqual(session.get_volume(), before * 0.25, places=2)
            ducker.request(False, 0.25, False)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and abs(session.get_volume() - before) > 0.001:
                time.sleep(0.05)
            self.assertAlmostEqual(session.get_volume(), before, places=3)
            ducker.request(True, 0.25, False);time.sleep(0.3)
        finally:
            ducker.close()
        self.assertAlmostEqual(session.get_volume(), before, places=3)
        self.assertFalse((folder / 'audio-ducking.json').exists())
        session.close()


if __name__ == '__main__':
    unittest.main()

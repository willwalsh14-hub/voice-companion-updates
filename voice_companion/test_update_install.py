"""Updates always go on to install (Will, October 29, 2026).

Will's update from 0.2.173 downloaded, then never installed. His update log showed:
- the close step took over three minutes;
- Voice Companion's own runtime files (base_library.zip, libcrypto-3.dll...) were in
  use, and he was told "Another program is using a Voice Companion file";
- then "The update speech files are missing." stopped the update before Setup ran,
  and the Updates folder was empty.

Only AppUpdates.remove_old_downloads deletes Updates folders, and it runs when
Voice Companion starts. So Voice Companion was started again while the update ran:
it deleted the update (installer and update voice), and its own runtime files were
the ones in use. These tests reproduce each part.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import app_updates
from app_updates import AppUpdates

HERE = Path(__file__).parent


def make_update(folder):
    update = Path(folder) / 'Updates' / '97ea58e5-test'
    (update / 'update-voice-runtime').mkdir(parents=True)
    (update / 'update-voice-runtime' / 'VoiceCompanion-UpdateVoice.exe').write_bytes(b'MZvoice')
    (update / 'VoiceCompanion-Setup.exe').write_bytes(b'MZsetup')
    return update


class RunningUpdateTests(unittest.TestCase):
    def test_starting_voice_companion_during_an_update_keeps_the_update(self):
        with tempfile.TemporaryDirectory() as folder:
            update = make_update(folder)
            # The update helper is running (this test process stands in for it).
            (Path(folder) / 'update-running.json').write_text(json.dumps({'pid': os.getpid(), 'started': time.time()}))
            AppUpdates(folder, '0.2.173-test')
            self.assertTrue((update / 'update-voice-runtime' / 'VoiceCompanion-UpdateVoice.exe').is_file(),
                            'A second start must not delete the update that is installing.')
            self.assertTrue((update / 'VoiceCompanion-Setup.exe').is_file())
            self.assertTrue(app_updates.update_running(folder))

    def test_old_updates_are_still_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            update = make_update(folder)
            AppUpdates(folder, '0.2.173-test')
            self.assertFalse(update.exists(), 'With no update running, old downloads are removed.')

    def test_a_marker_left_by_a_stopped_update_does_not_keep_old_downloads(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'update-running.json'
            # The helper's process is gone.
            finished = subprocess.Popen([sys.executable, '-c', 'pass']); finished.wait()
            marker.write_text(json.dumps({'pid': finished.pid, 'started': time.time()}))
            self.assertFalse(app_updates.update_running(folder))
            # Started too long ago: a helper never runs for an hour.
            marker.write_text(json.dumps({'pid': os.getpid(), 'started': time.time() - 3600}))
            self.assertFalse(app_updates.update_running(folder))
            for broken in ('not json', '[]', '{"pid": "x"}', '{"pid": -5, "started": 1}'):
                marker.write_text(broken)
                self.assertFalse(app_updates.update_running(folder), broken)
            update = make_update(folder)
            AppUpdates(folder, '0.2.173-test')
            self.assertFalse(update.exists())

    def test_voice_companion_waits_for_a_running_update_instead_of_starting(self):
        # main() checks before it loads anything from the install folder for long,
        # and the app the update restarts (--resume-after-update) always starts.
        import companion
        source = Path(companion.__file__).read_text(encoding='utf-8')
        main = source[source.index('\ndef main():'):]
        self.assertIn('update_running_elsewhere()', main)
        self.assertLess(main.index('update_running_elsewhere()'), main.index('UPDATES = AppUpdates('))
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(companion, 'APP', Path(folder)):
            (Path(folder) / 'update-running.json').write_text(json.dumps({'pid': os.getpid(), 'started': time.time()}))
            with mock.patch.object(companion.sys, 'argv', ['companion']):
                self.assertTrue(companion.update_running_elsewhere())
            with mock.patch.object(companion.sys, 'argv', ['companion', '--resume-after-update']):
                self.assertFalse(companion.update_running_elsewhere())
        self.assertIn('Voice Companion is being updated.', companion.UPDATE_RUNNING_MESSAGE)


class HelperWiringTests(unittest.TestCase):
    def setUp(self):
        self.helper = (HERE / 'apply-update.ps1').read_text(encoding='utf-8')

    def test_missing_update_speech_never_stops_the_update(self):
        self.assertNotIn("throw 'The update speech files are missing.'", self.helper)
        self.assertNotIn("throw 'The update speech window did not start.'", self.helper)
        self.assertNotIn("throw 'The update speech window did not become ready.'", self.helper)
        self.assertIn('function Start-UpdateVoice', self.helper)

    def test_helper_marks_the_update_as_running_until_it_restarts_the_app(self):
        self.assertIn("update-running.json", self.helper)
        restart = self.helper[self.helper.index('function Restart-Companion'):]
        restart = restart[:restart.index('\n}\n')]
        self.assertLess(restart.index('Clear-UpdateRunning'), restart.index('Start-Process'))


if __name__ == '__main__':
    unittest.main()

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


def powershell():
    if sys.platform == 'win32':
        return str(Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
    import shutil
    return os.environ.get('VC_PWSH') or shutil.which('pwsh')


FAKE_CLOSE_STEP = r"""
# Stand-in for close-companion.ps1: nothing runs from the install folder.
function Write-CloseLog([string]$message) { }
function Get-CompanionProcessList { @() }
function Invoke-CompanionClose { [pscustomobject]@{ Code = $script:closeCode; Names = @('node.exe') } }
function Resume-NativeHostManifests([string]$folder) { }
function Join-Names([string[]]$names) { $names -join ' and ' }
$script:closeCode = [int]$env:VC_CLOSE_CODE
"""


@unittest.skipUnless(powershell() and Path(powershell()).exists(), 'PowerShell is not installed')
class HelperRunTests(unittest.TestCase):
    """The real apply-update.ps1, with a stand-in Setup and close step."""

    def run_helper(self, voice=False, close_code=0):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        update = root / 'Updates' / 'id'; update.mkdir(parents=True)
        data = root / 'data'; data.mkdir()
        app = root / 'Programs' / 'Voice Companion' / 'VoiceCompanion.exe'; app.parent.mkdir(parents=True); app.write_bytes(b'MZapp')
        (update / 'apply-update.ps1').write_text((HERE / 'apply-update.ps1').read_text(encoding='utf-8'), encoding='utf-8')
        (update / 'close-companion.ps1').write_text(FAKE_CLOSE_STEP, encoding='utf-8')
        if sys.platform == 'win32':
            setup = update / 'VoiceCompanion-Setup.cmd'
            setup.write_text('@echo 50> "%~dp0install-percent.txt"\r\n@exit /b 0\r\n')
        else:
            setup = update / 'VoiceCompanion-Setup.sh'
            setup.write_text('#!/bin/sh\necho 50 > "$(dirname "$0")/install-percent.txt"\nexit 0\n'); setup.chmod(0o755)
        if voice:
            (update / 'update-voice-runtime').mkdir()   # present, but its program is missing
        import hashlib
        digest = hashlib.sha256(setup.read_bytes()).hexdigest()
        arguments = [powershell(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(update / 'apply-update.ps1'),
                     '-InstallerPath', str(setup), '-AppPath', str(app), '-PreviousProcessId', '-1', '-ExpectedHash', digest,
                     '-DataFolder', str(data), '-ReadyFile', str(update / 'ready.json'), '-NoRestart']
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=180, env={**os.environ, 'VC_CLOSE_CODE': str(close_code)})
        log = (data / 'update-install-log.txt').read_text(encoding='utf-8-sig') if (data / 'update-install-log.txt').exists() else ''
        outcome = json.loads((data / 'update-result.json').read_text(encoding='utf-8-sig'))
        return outcome, log, result, data

    def test_missing_update_voice_still_installs(self):
        # Will's log: "The update speech files are missing." and Setup never ran.
        outcome, log, result, data = self.run_helper(voice=True)
        self.assertTrue(outcome.get('succeeded'), (outcome, log, result.stdout, result.stderr))
        self.assertIn('The update voice is missing', log)
        self.assertIn('Setup ended with exit code 0', log)
        self.assertFalse((data / 'update-running.json').exists(), 'The running mark must be gone when the update ends.')

    def test_voice_companion_that_will_not_close_does_not_stop_the_update(self):
        outcome, log, result, data = self.run_helper(close_code=1)
        self.assertTrue(outcome.get('succeeded'), (outcome, log, result.stdout, result.stderr))
        self.assertIn('Setup closes it', log)

    def test_a_named_program_of_the_user_still_stops_the_update(self):
        outcome, log, result, data = self.run_helper(close_code=2)
        self.assertFalse(outcome.get('succeeded'))
        self.assertEqual(outcome.get('blocked_by'), ['node.exe'])

    def test_each_step_says_how_long_it_took(self):
        outcome, log, result, data = self.run_helper()
        for step in ('Closing the old Voice Companion took', 'The close step took'):
            self.assertIn(step, log)


class HelperWiringTests(unittest.TestCase):
    def setUp(self):
        self.helper = (HERE / 'apply-update.ps1').read_text(encoding='utf-8')

    def test_these_checks_run_in_the_windows_build(self):
        self.assertIn("'test_update_install'", (HERE / 'build-windows.ps1').read_text(encoding='utf-8'))

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

"""Before an update installs, close Voice Companion's own programs and nothing else.

Will's 0.2.171 update failed: Setup found VoiceCompanion.exe and four
VoiceCompanion-BrowserHost.exe processes, could not shut them down and stopped
(exit code 5). close-companion.ps1 now closes them before Setup, and Setup runs it
again itself. These tests run the real PowerShell functions: Windows PowerShell on
Windows, PowerShell 7 elsewhere when it is installed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / 'close-companion.ps1'
FOLDER = r'C:\Users\Will\AppData\Local\Programs\Voice Companion'


def powershell():
    if sys.platform == 'win32':
        return str(Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
    return os.environ.get('VC_PWSH') or shutil.which('pwsh')


def run_powershell(command, environment=None, timeout=120):
    shell = powershell()
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
                            capture_output=True, text=True, timeout=timeout, env={**os.environ, **(environment or {})})
    return result


def process(pid, parent, path, created, name=None):
    return {'ProcessId': pid, 'ParentProcessId': parent, 'Path': path,
            'Name': name or (path.rsplit('\\', 1)[-1] if path else 'System'), 'Created': '2026-10-28T10:%02d:00' % created}


def inside(relative):
    return FOLDER + '\\' + relative


EDGE = r'C:\Program Files (x86)\Microsoft\EdgeWebView\Application\130.0\msedgewebview2.exe'
POWERSHELL = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
UPDATES = r'C:\Users\Will\AppData\Local\VoiceCompanion\Updates\1234'

# The processes on Will's computer while an update starts.
PROCESSES = [
    process(4, 0, '', 0, 'System'),
    process(10, 4, r'C:\Windows\explorer.exe', 1),
    process(100, 10, inside('VoiceCompanion.exe'), 2),
    process(101, 100, inside(r'playwright\driver\node.exe'), 3),
    process(102, 101, inside(r'browser-runtime\chromium-1140\chrome-win\chrome.exe'), 4),
    process(103, 100, inside('VoiceCompanion-WebView2.exe'), 3),
    process(104, 103, EDGE, 4),
    process(105, 100, r'C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE', 3),
    process(106, 105, EDGE, 4),
    process(107, 100, r'C:\Program Files\Mozilla Thunderbird\thunderbird.exe', 3),
    process(108, 100, r'C:\Program Files\Google\Chrome\Application\chrome.exe', 3),
    process(109, 100, inside(r'espeak\espeak-ng.exe'), 5),
    process(110, 100, r'C:\Windows\System32\conhost.exe', 5),
    # A parent id reused by a newer process: this one is older, so not its child.
    process(111, 100, POWERSHELL, 1),
    process(200, 10, r'C:\Program Files\Google\Chrome\Application\chrome.exe', 1),
    process(201, 200, inside('VoiceCompanion-BrowserHost.exe'), 6),
    process(202, 201, inside('VoiceCompanion-BrowserHost.exe'), 6),
    process(203, 300, inside('VoiceCompanion-BrowserHost.exe'), 7),
    process(204, 203, inside('VoiceCompanion-BrowserHost.exe'), 7),
    process(300, 10, r'C:\Program Files\Mozilla Thunderbird\thunderbird.exe', 1),
    process(400, 10, r'C:\Program Files (x86)\NVDA\nvda.exe', 1),
    process(500, 10, inside('VoiceCompanion.exe').upper(), 8),
    process(600, 10, r'C:\Users\Will\AppData\Local\Programs\Voice Companion Old\VoiceCompanion.exe', 8),
    process(601, 10, r'C:\Users\Will\AppData\Local\Programs\Voice Companion Tools\tool.exe', 8),
    process(700, 10, r'C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE', 9),
    process(701, 700, inside(r'_internal\helper.exe'), 9),
    # The update: the helper (started by the app), the update voice and Setup.
    process(900, 100, POWERSHELL, 20),
    process(901, 900, UPDATES + r'\update-voice-runtime\VoiceCompanion-UpdateVoice.exe', 21),
    process(902, 900, UPDATES + r'\VoiceCompanion-Setup.exe', 22),
    process(903, 902, r'C:\Users\Will\AppData\Local\Temp\is-ABC.tmp\VoiceCompanion-Setup.tmp', 22),
    process(904, 903, POWERSHELL, 23),
    process(905, 900, r'C:\Windows\System32\conhost.exe', 20),
]


@unittest.skipUnless(powershell() and Path(powershell()).exists(), 'PowerShell is not installed')
class ProcessSelectionTests(unittest.TestCase):
    def select(self, processes, self_id):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(folder) / 'processes.json'
            fixture.write_text(json.dumps(processes), encoding='utf-8')
            command = ("$ErrorActionPreference='Stop'; . $env:VC_SCRIPT; "
                       "$items = @(Get-Content -Raw -LiteralPath $env:VC_FIXTURE | ConvertFrom-Json); "
                       "foreach ($item in $items) { $item.Created = [datetime]$item.Created }; "
                       "$chosen = @(Select-CompanionProcesses $items $env:VC_FOLDER ([int]$env:VC_SELF)); "
                       "ConvertTo-Json -Compress @(@($chosen | ForEach-Object { [int]$_.ProcessId }))")
            result = run_powershell(command, {'VC_SCRIPT': str(SCRIPT), 'VC_FIXTURE': str(fixture), 'VC_FOLDER': FOLDER, 'VC_SELF': str(self_id)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip() or '[]')

    def test_closes_every_voice_companion_program_and_its_hidden_helpers(self):
        chosen = self.select(PROCESSES, 900)
        # The app, a second copy, Playwright's driver and browser, WebView2 and its
        # Edge engine, eSpeak, the console window, and every browser host
        # (Chrome's and Thunderbird's, both processes of each).
        self.assertEqual(sorted(chosen), [100, 101, 102, 103, 104, 109, 110, 201, 202, 203, 204, 500, 701])

    def test_never_closes_the_users_programs(self):
        chosen = set(self.select(PROCESSES, 900))
        # Word, Thunderbird and Chrome opened by Voice Companion, the user's own
        # Chrome, Thunderbird, Word and NVDA, Explorer, and Word's own helpers.
        for pid in (105, 106, 107, 108, 200, 300, 400, 700, 10, 4):
            self.assertNotIn(pid, chosen)

    def test_similar_folder_names_and_reused_ids_are_left_alone(self):
        chosen = set(self.select(PROCESSES, 900))
        self.assertNotIn(600, chosen)   # Voice Companion Old
        self.assertNotIn(601, chosen)   # Voice Companion Tools
        self.assertNotIn(111, chosen)   # its parent id belongs to a newer process

    def test_the_update_itself_is_never_closed(self):
        # From the helper: itself, its update voice, Setup and its console stay.
        chosen = set(self.select(PROCESSES, 900))
        for pid in (900, 901, 902, 903, 904, 905):
            self.assertNotIn(pid, chosen)
        # From Setup's own close step: the helper that started Setup is an
        # ancestor, so it stays too, though the app started it.
        chosen = set(self.select(PROCESSES, 904))
        for pid in (900, 901, 902, 903, 904):
            self.assertNotIn(pid, chosen)
        self.assertIn(100, chosen)

    def test_children_are_closed_before_their_parents(self):
        chosen = self.select(PROCESSES, 900)
        for child, parent in ((101, 100), (102, 101), (104, 103), (103, 100), (202, 201), (204, 203)):
            self.assertLess(chosen.index(child), chosen.index(parent), (child, parent, chosen))

    def test_nothing_running_and_missing_paths(self):
        self.assertEqual(self.select([process(4, 0, '', 0, 'System')], 999), [])
        # Paths Windows will not show (other users, protected processes) are not ours.
        self.assertEqual(self.select([process(50, 4, None, 1, 'csrss.exe'), process(51, 50, '', 2, 'svchost.exe')], 999), [])


@unittest.skipUnless(powershell() and Path(powershell()).exists(), 'PowerShell is not installed')
class ClosingStepTests(unittest.TestCase):
    def test_powershell_closing_steps(self):
        # Gentle close, then force; restarted browser hosts; files held by the user's
        # programs named and waited for; browser host manifests paused and restored.
        result = run_powershell('& $env:VC_TEST', {'VC_TEST': str(HERE / 'test-update-close.ps1')}, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS:', result.stdout)

    @unittest.skipUnless(sys.platform == 'win32', 'Real Windows processes and file locks')
    def test_real_processes_and_locked_files_on_windows(self):
        result = run_powershell('& $env:VC_TEST', {'VC_TEST': str(HERE / 'test-update-close-windows.ps1')}, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS:', result.stdout)


class BlockedUpdateStartupTests(unittest.TestCase):
    """After an update stopped because the user's program held a file, the app says
    which program, and what to do, in its one startup announcement."""

    def test_notice_names_the_program_and_what_to_do(self):
        import startup_speech
        notice = startup_speech.update_notice({'succeeded': False, 'blocked_by': ['Microsoft Word']})
        self.assertEqual(notice, 'Microsoft Word was using a Voice Companion file, so the update could not install. '
                                 'Save your work in Microsoft Word and close it. Then choose Check for updates on the main menu.')
        two = startup_speech.update_notice({'succeeded': False, 'blocked_by': ['Notepad', 'Microsoft Word']})
        self.assertTrue(two.startswith('Microsoft Word and Notepad were using a Voice Companion file'), two)
        # Windows PowerShell writes one name as a plain string.
        self.assertTrue(startup_speech.update_notice({'succeeded': False, 'blocked_by': 'Notepad'}).startswith('Notepad was using'))
        for result in ({'succeeded': True}, {'succeeded': False}, {'succeeded': False, 'blocked_by': []},
                       {'succeeded': False, 'blocked_by': [None, 3, '']}, None, [], 'text'):
            self.assertEqual(startup_speech.update_notice(result), '', result)

    def test_startup_says_the_reason_after_the_result_with_the_microphone_off(self):
        import companion
        import startup_speech
        from unittest import mock
        from test_core_email_fixes import CompanionTestCase
        from test_startup_announcement import FakeUpdates

        class Case(CompanionTestCase):
            def runTest(inner):
                for name, value in dict(MICROPHONE_OFF=True, MICROPHONE_FAILED=False, STARTUP_UPDATE_PENDING=False,
                                        STARTUP_QUESTION=None, STARTUP_NOTICES=[], MAIN_MENU_LEAD='',
                                        UPDATE_RETURN_MODE='awake', PREFERENCES={'check_updates': True},
                                        UPDATES=FakeUpdates()).items():
                    patcher = mock.patch.object(companion, name, value, create=True); patcher.start(); inner.addCleanup(patcher.stop)
                with mock.patch.object(companion.sys, 'argv', ['companion', '--resume-after-update']):
                    (inner.folder / 'update-result.json').write_text(json.dumps(
                        {'succeeded': False, 'error': 'Files are in use by Microsoft Word.', 'blocked_by': ['Microsoft Word']}), encoding='utf-8')
                    # The microphone is off: the app starts on the main menu, not asleep.
                    inner.assertEqual(companion.announce_startup(), 'awake')
                inner.assertEqual(len(inner.said), 1, inner.said)
                text = inner.said[0]
                for piece in ('is ready.', startup_speech.UPDATE_RESULTS['failed'], 'Microsoft Word was using a Voice Companion file',
                              'Check for updates', startup_speech.MICROPHONE['off'], 'You are on the main menu.'):
                    inner.assertIn(piece, text)
                inner.assertLess(text.index('The update did not finish.'), text.index('Microsoft Word was using'))
                inner.assertLess(text.index('Microsoft Word was using'), text.index('The microphone is off'))
        result = unittest.TestResult()
        Case().run(result)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)


class UpdateCloseWiringTests(unittest.TestCase):
    def test_helper_closes_everything_before_setup_and_never_aborts_in_setup(self):
        helper = (HERE / 'apply-update.ps1').read_text(encoding='utf-8')
        body = helper[helper.index('\ntry {'):]
        self.assertIn("close-companion.ps1", helper)
        self.assertLess(body.index('Stop-PreviousCompanion'), body.index('Invoke-CompanionClose'))
        self.assertLess(body.index('Invoke-CompanionClose'), body.index('$setup = Start-Process'))
        self.assertIn('Resume-NativeHostManifests', body)

    def test_setup_runs_the_same_close_step_and_never_prompts(self):
        installer = (HERE / 'VoiceCompanion.iss').read_text(encoding='utf-8')
        self.assertIn('function PrepareToInstall', installer)
        self.assertIn("ExtractTemporaryFile('close-companion.ps1')", installer)
        self.assertIn('{sysnative}\\WindowsPowerShell\\v1.0\\powershell.exe', installer)
        self.assertIn('CloseApplications=force', installer)
        self.assertIn('RestartApplications=no', installer)
        self.assertIn('procedure DeinitializeSetup', installer)
        self.assertIn('.update-paused', installer)

    def test_close_script_ships_with_the_app_and_the_update(self):
        builder = (HERE / 'build-windows.ps1').read_text(encoding='utf-8')
        self.assertIn("'close-companion.ps1:.'", builder)
        self.assertIn("'test_update_close'", builder)


if __name__ == '__main__':
    unittest.main()

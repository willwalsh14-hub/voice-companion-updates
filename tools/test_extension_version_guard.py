"""A change to the browser extension must raise its manifest version.

Without a higher version the Chrome Web Store refuses the upload, and Chrome
never updates users' copies. The last test checks this branch against main.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import extension_version_guard as guard

ROOT = Path(__file__).resolve().parents[1]
FOLDER = 'voice_companion/regular_browser_extension'


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix='vc-guard-'))
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'config', 'user.email', 'test@example.com')
        git(self.repo, 'config', 'user.name', 'Test')
        self.extension = self.repo / FOLDER
        self.extension.mkdir(parents=True)
        self.manifest({'manifest_version': 3, 'version': '0.4.2', 'key': 'KEY'})
        (self.extension / 'worker.js').write_text('let a=1;\n')
        (self.extension / 'README.md').write_text('Notes.\n')
        (self.extension / 'test-worker.cjs').write_text('// test\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'base')
        git(self.repo, 'branch', 'base')

    def tearDown(self):
        subprocess.run(['rm', '-rf', str(self.repo)])

    def manifest(self, data):
        (self.extension / 'manifest.json').write_text(json.dumps(data, indent=2))

    def check(self):
        return guard.problem(self.repo, 'base')

    def test_no_change_is_fine(self):
        self.assertIsNone(self.check())

    def test_code_change_without_bump_is_caught(self):
        (self.extension / 'worker.js').write_text('let a=2;\n')
        message = self.check()
        self.assertIn('worker.js', message)
        self.assertIn('0.4.2', message)
        self.assertIn('manifest.json', message)

    def test_committed_change_without_bump_is_caught(self):
        (self.extension / 'worker.js').write_text('let a=2;\n')
        git(self.repo, 'commit', '-qam', 'change')
        self.assertIn('worker.js', self.check())

    def test_code_change_with_bump_is_fine(self):
        (self.extension / 'worker.js').write_text('let a=2;\n')
        self.manifest({'manifest_version': 3, 'version': '0.4.3', 'key': 'KEY'})
        self.assertIsNone(self.check())

    def test_lower_version_is_caught(self):
        (self.extension / 'worker.js').write_text('let a=2;\n')
        self.manifest({'manifest_version': 3, 'version': '0.4.1', 'key': 'KEY'})
        self.assertIn('0.4.1', self.check())

    def test_manifest_change_without_bump_is_caught(self):
        self.manifest({'manifest_version': 3, 'version': '0.4.2', 'key': 'KEY', 'permissions': ['tabs']})
        self.assertIn('manifest.json', self.check())

    def test_new_file_not_yet_added_to_git_is_caught(self):
        (self.extension / 'helper.js').write_text('// new\n')
        self.assertIn('helper.js', self.check())

    def test_deleted_file_is_caught(self):
        (self.extension / 'worker.js').unlink()
        self.assertIn('worker.js', self.check())

    def test_notes_and_tests_are_not_sent_to_chrome_so_need_no_bump(self):
        (self.extension / 'README.md').write_text('More notes.\n')
        (self.extension / 'test-worker.cjs').write_text('// more\n')
        self.assertIsNone(self.check())

    def test_command_line_reports_a_github_error(self):
        (self.extension / 'worker.js').write_text('let a=2;\n')
        env = dict(os.environ, GITHUB_ACTIONS='true')
        result = subprocess.run(['python3', str(Path(guard.__file__)), '--base', 'base', '--root', str(self.repo)],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn('::error title=Browser extension version not raised::', result.stdout)

    def test_unknown_base_is_reported_not_ignored(self):
        with self.assertRaises(guard.GuardError):
            guard.problem(self.repo, 'no-such-branch')


class ThisBranchTests(unittest.TestCase):
    def test_this_branch_raises_the_version_when_the_extension_changed(self):
        base = 'origin/main'
        known = subprocess.run(['git', 'rev-parse', '--verify', '-q', base], cwd=ROOT, capture_output=True)
        if known.returncode:
            self.skipTest('origin/main is not available here.')
        message = guard.problem(ROOT, base)
        self.assertIsNone(message, message)


if __name__ == '__main__':
    unittest.main()

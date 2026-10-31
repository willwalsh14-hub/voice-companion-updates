"""Every test file runs in the Windows build (build-windows.ps1), or is named here with the reason.

Found October 30, 2026: three new test files passed when run alone but were never in the
build's list, so neither the Windows build nor tools/run_checks.py ran them."""
import re
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'voice_companion'
# Not in the build on purpose. Add a file here only with its reason.
NOT_IN_BUILD = {
    'test_options_dialog_windows': 'tried on a claude/try- branch when Options change (its docstring says so)',
}


class ReleaseListTests(unittest.TestCase):
    def test_every_test_file_runs_in_the_windows_build(self):
        script = (SOURCE / 'build-windows.ps1').read_text(encoding='utf-8')
        named = set(re.findall(r"'(test_\w+?)(?:\.\w+)?'", script))
        files = {path.stem for path in SOURCE.glob('test_*.py')}
        missing = sorted(files - named - set(NOT_IN_BUILD))
        self.assertEqual(missing, [], 'Add these to the release list in build-windows.ps1: ' + ', '.join(missing))
        self.assertEqual(sorted(set(NOT_IN_BUILD) - files), [], 'Remove names of test files that no longer exist.')


if __name__ == '__main__':
    unittest.main()

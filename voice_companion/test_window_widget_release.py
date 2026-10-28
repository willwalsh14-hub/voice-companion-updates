"""Every Tk widget kept on the CompanionWindow must be released on the window thread.

A widget kept as self.<name> and not cleared at close is freed later by the test or
main thread, and Python stops with "Tcl_AsyncDelete: async handler deleted by the
wrong thread" (CLAUDE.md, Windows test lessons). In 0.2.173 self.hint_label did this,
and every real-window test after the first one in a process crashed on Windows."""
import re
import unittest
from pathlib import Path


class WidgetReleaseTests(unittest.TestCase):
    def test_widgets_kept_on_the_window_are_released_at_close(self):
        source = (Path(__file__).with_name('app_window.py')).read_text(encoding='utf-8')
        run = source[source.index('    def _run(self'):]
        widgets = set(re.findall(r'\b([a-z_][a-z0-9_]*)\s*=\s*(?:tk|scrolledtext|tkfont)\.[A-Z][A-Za-z]*\(', run))
        kept = {attribute for attribute, value in re.findall(r'self\.([a-z_][a-z0-9_]*)\s*=\s*([a-z_][a-z0-9_]*)\b', run)
                if value in widgets or value == 'root'}
        kept |= set(re.findall(r'self\.([a-z_][a-z0-9_]*)\s*=\s*(?:tk|scrolledtext|tkfont)\.', run))
        cleanup = source[source.index('            self.closed.set()'):source.index('            gc.collect()')]
        released = set(re.findall(r'self\.([a-z_][a-z0-9_]*)\s*=', cleanup))
        self.assertTrue(kept, 'no widgets found; the check needs updating')
        self.assertEqual(sorted(kept - released), [], 'release these on the window thread at close')


if __name__ == '__main__':
    unittest.main()

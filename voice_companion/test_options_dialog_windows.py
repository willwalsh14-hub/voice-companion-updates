"""Windows only: opening an Options category says "<category> dialog", and nothing cuts it off.

Will: "Every option, when pressing Enter on the category, should have the word dialog
after it." File manager options starts with an editable combo box. Windows gives focus
to the edit box inside it, and the focus watcher took that for a focus change and spoke
the field again, stopping "File manager options dialog" just after it began.

Not in the release list yet: try it on a claude/try- branch first (CLAUDE.md)."""
import sys
import time
import unittest

from settings_model import CATEGORIES
from test_native_settings import NativeSettingsTests


@unittest.skipUnless(sys.platform == 'win32', 'Native Windows controls require Windows')
class OptionsDialogWordTests(NativeSettingsTests):
    def test_every_category_says_dialog_and_is_not_spoken_over(self):
        hwnd = self.open_panel()
        categories = self.u.GetDlgItem(hwnd, 100)
        for index, name in enumerate(CATEGORIES):
            with self.subTest(category=name):
                self.u.SendMessageW(categories, 0x186, index, 0)
                self.session.category = index
                before = len(self.notices)
                self.u.PostMessageW(categories, 0x100, 0x0D, 0)
                self.wait(lambda: any(n.startswith(name + ' dialog.') for n in self.notices[before:]))
                time.sleep(.5)      # several focus-watch timer ticks
                self.assertTrue(self.notices[-1].startswith(name + ' dialog.'), self.notices[before:])
                self.panel.command('cancel')
                self.wait(lambda: self.u.IsWindowVisible(categories))


del NativeSettingsTests      # run only the test above from this file

if __name__ == '__main__':
    unittest.main()

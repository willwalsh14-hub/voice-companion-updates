"""Opening Notepad lands on the Notepad menu (Will, October 2026).

Items: Create a note (Control+N), Open a note (Control+O), Options (Alt+O), Main menu
(Control+F1). Escape in a note saves it and goes back to this menu."""
import unittest
from unittest import mock

import companion
from test_core_email_fixes import CompanionTestCase


class NotepadMenuTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        for name, value in dict(TASK_NAV=None, NAMED_LIST=None, OPEN_NOTE=None).items():
            p = mock.patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(companion, 'note_buffer', []); p.start(); self.addCleanup(p.stop)

    def key(self, key, mode):
        request = companion.navigation_key(key, mode)
        return mode if request is None else companion.handle(request, mode)

    def open_menu(self):
        mode = companion.handle('notepad', 'awake')
        self.assertEqual(mode, 'notepad_menu')
        return mode

    def test_notepad_opens_its_menu_with_four_items(self):
        self.open_menu()
        self.assertTrue(self.said[-1].startswith('Notepad menu. Create a note'), self.said[-1])
        names = [name for name, _, _ in companion.task_navigation().items('notepad_menu')]
        self.assertEqual(names, ['Create a note', 'Open a note', 'Options', 'Main menu'])
        self.assertEqual(dict(companion.MAIN_MENU_CHOICES)['Notepad'], 'notepad')

    def test_arrows_stop_at_the_ends(self):
        mode = self.open_menu()
        mode = self.key('Up', mode)
        self.assertTrue(self.said[-1].startswith('Start of list.'), self.said[-1])
        for _ in range(4): mode = self.key('Down', mode)
        self.assertTrue(self.said[-1].startswith('End of list.'), self.said[-1])

    def test_create_a_note_then_escape_saves_and_returns_to_the_menu(self):
        mode = self.key('Ctrl+N', self.open_menu())
        self.assertEqual(mode, 'note')
        mode = companion.handle('Buy milk.', mode)
        with mock.patch.object(companion, 'save_note', return_value=True) as save:
            mode = self.key('Escape', mode)
        self.assertEqual(mode, 'notepad_menu')
        self.assertTrue(save.called)
        self.assertTrue(self.said[-1].startswith('Note saved. Notepad menu.'), self.said[-1])
        self.assertEqual(self.key('Escape', mode), 'awake')

    def test_open_a_note_lists_notes_over_the_menu(self):
        with mock.patch.object(companion, 'list_saved_notes') as listing:
            mode = self.key('Ctrl+O', self.open_menu())
        self.assertEqual(mode, 'notepad_menu')
        self.assertTrue(listing.called)

    def test_enter_on_main_menu_and_control_f1(self):
        mode = self.open_menu()
        for _ in range(3): mode = self.key('Down', mode)
        self.assertEqual(self.key('Enter', mode), 'awake')
        self.assertEqual(self.key('Ctrl+F1', self.open_menu()), 'awake')

    def test_saying_main_menu_in_a_note_still_goes_to_the_main_menu(self):
        mode = self.key('Ctrl+N', self.open_menu())
        self.assertEqual(companion.handle('main menu', mode), 'awake')


if __name__ == '__main__':
    unittest.main()

"""Every Options control says what kind it is (Will, October 30, 2026): check box, combo box,
edit, button, list box. Before, a check box said "Announce email header names, off"."""
import unittest

from native_settings import BUTTON_WORDS, ROLES, check_words, control_words
from settings_model import CATEGORIES, Field, fields


class ControlWordsTests(unittest.TestCase):
    def test_each_kind_says_its_role(self):
        self.assertEqual(control_words(Field('a', 'Announce email header names', 'check'), True),
                         'Announce email header names, check box, checked.')
        self.assertEqual(control_words(Field('a', 'Announce email header names', 'check'), False),
                         'Announce email header names, check box, not checked.')
        self.assertEqual(control_words(Field('v', 'Verbosity', 'choice', ('high', 'medium', 'low')), 'medium', 1, 3),
                         'Verbosity, combo box, medium, 2 of 3.')
        self.assertEqual(control_words(Field('v', 'Sending account', 'choice', ()), ''), 'Sending account, combo box, unavailable.')
        self.assertEqual(control_words(Field('f', 'Font for new documents', 'combo', ('Arial',)), 'Arial'),
                         'Font for new documents, edit combo box, Arial.')
        self.assertEqual(control_words(Field('d', 'Download folder', 'text'), ''), 'Download folder, edit, blank.')
        self.assertEqual(control_words(Field('d', 'Download folder', 'text'), 'C:\\Downloads'),
                         'Download folder, edit, C:\\Downloads.')
        self.assertEqual(control_words(Field('k', 'API key', 'password'), 'secret'), 'API key, password edit, hidden.')
        self.assertNotIn('secret', control_words(Field('k', 'API key', 'password'), 'secret'))
        self.assertEqual(control_words(Field('x', 'Add an email account', 'action'), None), 'Add an email account, button.')
        self.assertEqual(check_words(True), 'checked')
        self.assertTrue(BUTTON_WORDS['OK'].startswith('OK, button.'))

    def test_every_option_in_every_category_has_a_role(self):
        context = {'accounts': ('thunderbird me@example.com',), 'voices': (), 'podcasts': ()}
        for category in CATEGORIES:
            for field in fields(category, context):
                with self.subTest(category=category, field=field.key):
                    self.assertIn(field.kind, ROLES)
                    words = control_words(field, False if field.kind == 'check' else '', 0, len(field.choices) or 1)
                    self.assertIn(ROLES[field.kind], words)
                    self.assertTrue(words.endswith('.'))
                    self.assertNotIn('..', words)


if __name__ == '__main__':
    unittest.main()

"""Edit fields in a real browser: arrowing or tabbing onto one makes it ready to type.

Will's rules (October 2026), as in builds before 0.2.158:
- Edit fields of every kind (text, search, email, address, phone, number,
  password, multi-line, contenteditable and role textbox) and editable combo
  boxes (a site's search box with suggestions): Up or Down Arrow onto one, or
  Tab onto one, and typing goes straight into it.
- Combo boxes you pick from (select, list-only combo boxes) and sliders:
  arrowing past only reads them, so a value can never change by accident.
  Enter or Tab starts using them.
These checks drive the same WebSession code the window keys use.
"""
import os
import tempfile
import unittest

from web_assistant import BrowserBackend, WebSession
from web_browse import editable_combo, kind

EDIT_FIELDS = (
    ('text', '<label>Full name<input id="f"></label>', 'Full name'),
    ('search', '<label>Find<input type="search" id="f"></label>', 'Find'),
    ('email', '<label>Email<input type="email" id="f"></label>', 'Email'),
    ('url', '<label>Website<input type="url" id="f"></label>', 'Website'),
    ('tel', '<label>Phone<input type="tel" id="f"></label>', 'Phone'),
    ('number', '<label>Age<input type="number" id="f"></label>', 'Age'),
    ('password', '<label>Password<input type="password" id="f"></label>', 'Password'),
    ('textarea', '<label>Notes<textarea id="f"></textarea></label>', 'Notes'),
    ('contenteditable', '<div id="f" contenteditable="true" aria-label="Message"></div>', 'Message'),
    ('role textbox', '<div id="f" role="textbox" contenteditable="true" tabindex="0" aria-label="Comment"></div>', 'Comment'),
    ('editable combo box', '<label for="f">Search videos</label><input id="f" role="combobox" aria-expanded="false" aria-autocomplete="list">', 'Search videos'),
    ('suggestion list', '<label>City<input id="f" list="cities"></label><datalist id="cities"><option value="Boston"></datalist>', 'City'),
)

# Real pages put line breaks and spaces between tags.
PAGE = '<title>Form</title>\n<h1>Form page</h1>\n  <p>Text before the field.</p>\n  <div>\n    {field}\n  </div>\n  <p>Text after the field.</p>\n'


class EditFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls.driver = sync_playwright().start()
        except Exception as error:
            raise unittest.SkipTest('Playwright is unavailable: ' + str(error))
        try:
            cls.browser = cls.driver.chromium.launch(headless=True)
        except Exception as error:
            cls.driver.stop()
            if os.getenv('VOICE_COMPANION_REQUIRE_WEB_TESTS'):
                raise
            raise unittest.SkipTest('Chromium is unavailable: ' + str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.driver.stop()

    def setUp(self):
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.page = self.context.new_page(); self.page.set_default_timeout(5000)
        self.backend = BrowserBackend(tempfile.gettempdir()); self.backend.page = self.page; self.backend.context = self.context
        self.backend.start = lambda: None
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = WebSession(self.temp.name, self.backend); self.session.auto_read = False

    def load(self, markup):
        self.page.set_content(PAGE.format(field=markup))
        self.session._refresh(read=False)
        self.session.keyboard('PageTop')

    def arrow_onto(self, label):
        for _ in range(6):
            said = self.session.keyboard('Read:Down')
            item = self.session.browse.current()
            if item and item.get('label') == label:
                return said
        self.fail('Down Arrow never reached ' + label)

    def type_and_save(self, text):
        field = self.session.keyboard_field
        self.assertIsNotNone(field, 'the field is not ready to type')
        self.session.dictate_to_focus(text, literal=True)
        self.session.keyboard('Tab')
        return self.page.evaluate("()=>{const e=document.getElementById('f');return e.isContentEditable?e.innerText:e.value}")

    def test_arrowing_onto_every_kind_of_edit_field_makes_it_ready_to_type(self):
        for name, markup, label in EDIT_FIELDS:
            with self.subTest(field=name):
                self.load(markup)
                said = self.arrow_onto(label)
                self.assertIn(label, said)
                self.assertIsNotNone(self.session.keyboard_field, name)
                self.assertEqual(self.session.keyboard_field['label'], label)
                typed = '42' if name == 'number' else 'hello'
                self.assertEqual(self.type_and_save(typed).strip(), typed)

    def test_tabbing_onto_every_kind_of_edit_field_makes_it_ready_to_type(self):
        for name, markup, label in EDIT_FIELDS:
            with self.subTest(field=name):
                self.load(markup)
                said = self.session.keyboard('Tab')
                self.assertIn(label, said)
                self.assertIsNotNone(self.session.keyboard_field, name)
                self.assertEqual(self.session.keyboard_field['label'], label)
                typed = '7' if name == 'number' else 'hi there'
                self.assertEqual(self.type_and_save(typed).strip(), typed)

    def test_arrows_leave_a_field_at_its_edges_and_shift_tab_comes_back(self):
        self.load('<label>Full name<input id="f"></label>')
        self.arrow_onto('Full name')
        self.assertIn('Text after the field.', self.session.keyboard('Leave:Down'))
        self.assertIsNone(self.session.keyboard_field)
        self.session.keyboard('Read:Up')
        self.assertIsNotNone(self.session.keyboard_field)
        self.assertIn('Text before the field.', self.session.keyboard('Leave:Up'))
        self.session.keyboard('PageBottom')
        self.assertIn('Full name', self.session.keyboard('ShiftTab'))
        self.assertIsNotNone(self.session.keyboard_field)

    def test_fields_are_ready_after_app_mode_is_switched_on_and_off(self):
        self.load('<label>Full name<input id="f"></label><label>Email<input type="email" id="g"></label>')
        self.assertEqual(self.session.keyboard('ToggleAppMode'), 'App mode.')
        self.assertTrue(self.session.keyboard('ToggleAppMode').startswith('Browse mode.'))
        self.session.keyboard('PageTop')
        self.arrow_onto('Full name')
        self.assertEqual(self.type_and_save('Will'), 'Will')
        self.session.keyboard('PageTop')
        self.session.keyboard('ToggleAppMode'); self.session.keyboard('ToggleAppMode')
        self.assertIn('Full name', self.session.keyboard('Tab'))
        self.assertIsNotNone(self.session.keyboard_field)

    def test_arrowing_past_a_select_only_reads_it_and_never_changes_it(self):
        self.load('<label>Size<select id="f"><option>Small</option><option>Medium</option><option>Large</option></select></label>')
        said = self.arrow_onto('Size')
        self.assertIn('Size', said); self.assertIn('Small', said)
        self.assertIsNone(self.session.keyboard_field)
        self.assertIn('Text after the field.', self.session.keyboard('Read:Down'))
        self.assertIn('Size', self.session.keyboard('Read:Up'))
        self.assertIsNone(self.session.keyboard_field)
        self.assertIn('Text before the field.', self.session.keyboard('Read:Up'))
        self.assertEqual(self.page.evaluate("document.getElementById('f').value"), 'Small')
        # Enter starts using it; Down then picks the next option.
        self.arrow_onto('Size')
        self.session.keyboard('Activate')
        self.assertIsNotNone(self.session.keyboard_field)
        self.assertEqual(self.session.keyboard('Down'), 'Medium.')
        self.assertEqual(self.page.evaluate("document.getElementById('f').value"), 'Medium')

    def test_tab_onto_a_select_starts_using_it(self):
        self.load('<label>Size<select id="f"><option>Small</option><option>Large</option></select></label>')
        self.assertIn('Size', self.session.keyboard('Tab'))
        self.assertIsNotNone(self.session.keyboard_field)
        self.assertEqual(self.session.keyboard('Down'), 'Large.')

    def test_a_list_only_combo_box_is_read_by_arrows_and_used_by_enter(self):
        self.load('<div id="f" role="combobox" aria-label="Color" tabindex="0" aria-expanded="false" aria-controls="colors">Red</div>'
                  '<div id="colors" role="listbox" hidden><div role="option">Red</div><div role="option">Blue</div></div>')
        self.arrow_onto('Color')
        item = self.session.browse.current()
        self.assertEqual(kind(item), 'combobox'); self.assertFalse(editable_combo(item))
        self.assertIsNone(self.session.keyboard_field)
        self.assertIn('Text after the field.', self.session.keyboard('Read:Down'))
        self.assertIsNone(self.session.keyboard_field)

    def test_arrowing_past_a_slider_only_reads_it(self):
        self.load('<label>Volume<input id="f" type="range" min="0" max="10" value="5"></label>')
        said = self.arrow_onto('Volume')
        self.assertIn('Volume', said)
        self.assertIsNone(self.session.slider_field); self.assertIsNone(self.session.keyboard_field)
        self.assertIn('Text after the field.', self.session.keyboard('Read:Down'))
        self.assertEqual(self.page.evaluate("document.getElementById('f').value"), '5')
        self.session.keyboard('Read:Up')
        self.assertEqual(self.session.keyboard('Activate'), 'Volume, slider, 5.')
        self.assertEqual(self.session.keyboard('Slider:Right'), '6.')


if __name__ == '__main__':
    unittest.main()

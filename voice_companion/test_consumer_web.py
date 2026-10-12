"""Consumer tasks in real browsers, using isolated sites and no real accounts."""
import os,random,sys,unittest
from pathlib import Path
import test_web_live as live_helpers
from web_browse import kind

class ConsumerWebTests(unittest.TestCase):
    setUpClass=classmethod(live_helpers.LiveWebTests.setUpClass.__func__)
    tearDownClass=classmethod(live_helpers.LiveWebTests.tearDownClass.__func__)
    setUp=live_helpers.LiveWebTests.setUp

    def load(self,html):
        self.page.set_content('<html><title>Consumer task</title><body>'+html+'</body></html>')
        self.session._refresh()

    def test_every_field_on_large_form_is_available(self):
        self.load(''.join(f'<label for="f{i}">Question {i}</label><input id="f{i}">' for i in range(45)))
        self.session.command('list fields')
        self.assertEqual(len(self.session.choices),45)
        for _ in range(44):self.session.command('next field')
        self.assertIn('Question 44',self.session.command('read current field') if self.session.keyboard_field else self.session.choices[self.session.field_list_index]['label'])

    def test_disabled_fields_are_not_guided_answers(self):
        self.load('<label for="disabled">Unavailable</label><input id="disabled" disabled><input aria-label="Read only" readonly value="Unchanged"><label for="name">Name</label><input id="name">')
        result=self.session.command('fill out this form')
        self.assertIn('Name',result);self.assertNotIn('Unavailable',result)
        result=self.session.command('Will')
        self.assertEqual(self.page.locator('#name').input_value(),'Will')
        self.assertNotIn('field changed',result.lower())
        self.assertEqual(self.page.locator('[readonly]').input_value(),'Unchanged')

    def test_contenteditable_and_custom_checkboxes_are_guided(self):
        self.load('<div contenteditable="true" aria-label="Comment"></div><div role="checkbox" tabindex="0" aria-label="Agree" aria-checked="false" onclick="this.setAttribute(\'aria-checked\',this.getAttribute(\'aria-checked\')===\'true\'?\'false\':\'true\')">Agree</div>')
        self.assertIn('Comment',self.session.command('fill out this form'))
        self.assertIn('Agree',self.session.command('Accessible comment'))
        self.session.command('yes')
        self.assertEqual(self.page.locator('[contenteditable]').inner_text(),'Accessible comment')
        self.assertEqual(self.page.locator('[role="checkbox"]').get_attribute('aria-checked'),'true')

    def test_fields_and_favorites_can_be_closed_by_voice(self):
        for name in ('list fields','list favorites'):
            with self.subTest(name=name):
                self.load('<h1>Account</h1><label for="name">Name</label><input id="name">')
                self.session.command('save favorite as Account')
                self.session.command(name)
                result=self.session.command('close list')
                self.assertEqual(self.session.list_focus,None)
                self.assertIn('page view',result.lower())
                self.session.command('go to beginning of page')
                self.assertIn('heading',self.session.command('next heading'))

    def test_conditional_form_fields_are_prompted_after_checkbox(self):
        self.load('<label><input id="delivery" type="checkbox" onchange="if(this.checked)document.getElementById(\'extra\').innerHTML=\'<label for=address>Delivery address</label><input id=address>\'">Delivery</label><div id="extra"></div>')
        self.assertIn('Delivery',self.session.command('fill out this form'))
        self.assertIn('Delivery address',self.session.command('yes'))
        self.session.command('123 Example Street')
        self.assertEqual(self.page.locator('#address').input_value(),'123 Example Street')

    def test_readonly_field_does_not_accept_voice_dictation(self):
        self.load('<label for="readonly">Account number</label><input id="readonly" readonly value="1234">')
        self.session.command('next form control')
        self.assertIn('read-only',self.session.dictate_to_focus('changed'))
        self.assertIn('read-only',self.session.command('type changed'))
        self.assertEqual(self.page.locator('#readonly').input_value(),'1234')

    def test_voice_field_and_favorite_boundaries_are_explicit(self):
        self.load('<label for="name">Name</label><input id="name">')
        for command in ('list fields','list favorites'):
            with self.subTest(command=command):
                self.session.command('save favorite as Account')
                self.assertIn('Start and end of list',self.session.command(command))
                self.assertIn('End of list',self.session.command('next'))
                self.assertIn('Start of list',self.session.command('previous'))
                self.session.command('close list')

    def test_different_sites_and_back_do_not_keep_old_page_elements(self):
        self.context.route('https://second.test/**',lambda r:r.fulfill(content_type='text/html',body='<title>Second site</title><h1>Second heading</h1><p>Second text</p>'))
        self.context.route('https://first.test/**',lambda r:r.fulfill(content_type='text/html',body='<title>First site</title><h1>First heading</h1><a href="https://second.test/">Visit second site</a>'))
        self.session.open('https://first.test/')
        self.assertIn('Visit second site',self.session.keyboard('Quick:k'))
        self.assertIn('Second site',self.session.keyboard('Activate'))
        self.assertNotIn('First heading',self.session.reading.text)
        self.assertIn('First site',self.session.command('go back'))
        self.assertNotIn('Second heading',self.session.reading.text)
        self.assertIsNone(self.session.keyboard_field)

    def test_handsfree_editing_inserts_at_spoken_caret(self):
        self.load('<label for="notes">Notes</label><textarea id="notes">abc</textarea>')
        self.session.command('next form control')
        self.session.command('next character');self.session.command('next character')
        self.session.dictate_to_focus('X',literal=True)
        self.session.command('save field')
        self.assertEqual(self.page.locator('#notes').input_value(),'abXc')

    def test_multiline_voice_edit_does_not_submit_or_erase_other_fields(self):
        self.load('<form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1"><label for="name">Name</label><input id="name" value="Will"><label for="notes">Notes</label><textarea id="notes"></textarea><button type="submit">Send</button></form>')
        self.session.command('next form control');self.session.command('next form control')
        self.session.dictate_to_focus('First line',literal=True)
        self.session.command('new line');self.session.dictate_to_focus('Second line',literal=True)
        self.session.command('save field')
        self.assertEqual(self.page.locator('#notes').input_value(),'First line\nSecond line')
        self.assertEqual(self.page.locator('#name').input_value(),'Will')
        self.assertEqual(self.page.evaluate('window.sent||0'),0)

    def test_voice_selection_scratch_and_replacement_work_before_save(self):
        self.load('<label for="notes">Notes</label><textarea id="notes"></textarea>')
        self.session.command('next form control')
        self.session.dictate_to_focus('one two three',literal=True)
        self.assertIn('Selected all',self.session.command('select all'))
        self.assertIn('one two three',self.session.command('read selection'))
        self.session.dictate_to_focus('Replacement',literal=True)
        self.assertEqual(self.session.keyboard_field['value'],'Replacement')
        self.session.command('select all');self.session.command('scratch')
        self.assertEqual(self.session.keyboard_field['value'],'')
        self.session.dictate_to_focus('Final',literal=True);self.session.command('save field')
        self.assertEqual(self.page.locator('#notes').input_value(),'Final')

    def test_typed_caret_edit_clears_old_voice_selection(self):
        import companion as app
        from unittest.mock import patch
        self.load('<label for="notes">Notes</label><textarea id="notes">abc</textarea>')
        self.session.command('next form control');self.session.command('select all')
        with patch.object(app,'web_session',self.session),patch.object(app,'APP_WINDOW',None),patch.object(app,'EDIT_ACK',{}):
            context=app.app_context('web')
            self.assertTrue(app.apply_keyboard_edit({'source':context['source'],'before':'abc','after':'abc','caret':1,'selection':None,'seq':1},'web'))
        self.session.dictate_to_focus('X',literal=True);self.session.command('save field')
        self.assertEqual(self.page.locator('#notes').input_value(),'aXbc')

    def test_private_field_requires_private_entry_and_never_reads_value(self):
        self.load('<label for="secret">Password</label><input id="secret" type="password" value="fixture-secret">')
        self.session.command('next form control')
        responses=[self.session.command('read current field'),self.session.command('read current word'),self.session.dictate_to_focus('dummy')]
        self.assertNotIn('fixture-secret',' '.join(responses))
        self.assertIn('private',responses[-1].lower())
        self.assertNotIn('fixture-secret',str(self.session.snapshot))

    def test_long_page_random_keyboard_voice_moves_keep_cursor_in_bounds(self):
        self.load(''.join(f'<h2>Section {i}</h2><p>Paragraph {i} has three words.</p><a href="https://fixture.test/next">Article {i}</a>' for i in range(40)))
        rng=random.Random(731)
        for _ in range(160):
            if rng.random()<.5:
                self.session.keyboard(rng.choice(['Quick:h','Quick:Shift:h','Quick:k','Quick:Shift:k','Read:Down','Read:Up','Read:Left','Read:Right','PageTop','PageBottom']))
            else:self.session.command(rng.choice(['next heading','previous heading','next link','previous link','next word','previous word','next paragraph','previous paragraph']))
            self.assertGreaterEqual(self.session.reading.position,0)
            self.assertLessEqual(self.session.reading.position,len(self.session.reading.text))
            self.assertEqual(self.session.reading.position,self.session.browse.position)

    def test_page_replacement_invalidates_staged_entry(self):
        self.load('<label for="name">Name</label><input id="name">')
        self.session.command('next form control');self.session.dictate_to_focus('Old answer',literal=True)
        self.page.set_content('<h1>Replacement</h1><label for="name">Different question</label><input id="name">')
        self.session.refresh_changed()
        self.assertIsNone(self.session.keyboard_field)
        self.assertEqual(self.page.locator('#name').input_value(),'')

@unittest.skipUnless(sys.platform=='win32','Windows Chrome consumer workflows')
class ChromeConsumerWebTests(ConsumerWebTests):
    browser_name='chrome'
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.driver=sync_playwright().start()
        try:
            if cls.browser_name=='brave':
                roots=[os.environ.get('PROGRAMFILES',''),os.environ.get('PROGRAMFILES(X86)',''),os.environ.get('LOCALAPPDATA','')]
                program=next(Path(root)/'BraveSoftware/Brave-Browser/Application/brave.exe' for root in roots if (Path(root)/'BraveSoftware/Brave-Browser/Application/brave.exe').is_file())
                cls.browser=cls.driver.chromium.launch(executable_path=str(program),headless=True)
            else:cls.browser=cls.driver.chromium.launch(channel=cls.browser_name if cls.browser_name!='edge' else 'msedge',headless=True)
        except Exception:
            cls.driver.stop()
            if os.getenv('VOICE_COMPANION_REQUIRE_BROWSER_CHANNELS'):raise
            raise unittest.SkipTest('Requested browser is not installed.')

@unittest.skipUnless(sys.platform=='win32','Windows Edge consumer workflows')
class EdgeConsumerWebTests(ChromeConsumerWebTests):browser_name='edge'

@unittest.skipUnless(sys.platform=='win32','Windows Brave consumer workflows')
class BraveConsumerWebTests(ChromeConsumerWebTests):browser_name='brave'

if __name__=='__main__':unittest.main()

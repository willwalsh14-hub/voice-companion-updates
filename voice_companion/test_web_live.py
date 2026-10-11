"""Real Firefox DOM tests using synthetic HTTPS pages; no accounts or real submissions."""
import os,sys,tempfile,unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from web_assistant import BrowserBackend,WebSession,WebError
from web_browse import BrowseCursor
PAGE='''<html><title>Fixture</title><body><h1 id="title">Fixture heading</h1><p>Readable text.</p><a href="https://fixture.test/next">Next page</a><form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1"><label for="name">Name</label><input id="name" required><label for="secret">Password</label><input id="secret" type="password"><label><input type="checkbox" id="agree">Agree</label><label><input type="radio" name="plan" value="a" required>Plan A</label><label><input type="radio" name="plan" value="b" required checked>Plan B</label><label>Choice<select><option>One</option><option>Two</option></select></label><label for="notes">Notes</label><textarea id="notes"></textarea><input type="submit" value="Submit application"><input disabled required aria-label="Unavailable"></form><table><caption>Prices</caption><tr><th scope="col">Product</th><th scope="col">Cost</th></tr><tr><th scope="row">Apple</th><td>One dollar</td></tr><tr><td colspan="2">Summary</td></tr></table><div aria-hidden="true"><a href="https://fixture.test/hidden">Hidden link</a></div><div id="shadow"></div><iframe src="https://fixture.test/frame"></iframe><script>document.getElementById('shadow').attachShadow({mode:'open'}).innerHTML='<button aria-labelledby="slabel"><span id="slabel">Shadow action</span></button>';window.sent=0;</script></body></html>'''
class LiveWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver=sync_playwright().start()
        if not Path(cls.driver.firefox.executable_path).is_file():
            cls.driver.stop()
            if os.getenv('VOICE_COMPANION_REQUIRE_WEB_TESTS'):raise RuntimeError('Firefox runtime is required for web release checks.')
            raise unittest.SkipTest('Install Firefox runtime for live DOM checks.')
        options={'headless':True,'timeout':15000}
        if sys.platform!='win32':options['env']={**os.environ,'MOZ_DISABLE_CONTENT_SANDBOX':'1','MOZ_DISABLE_RDD_SANDBOX':'1','MOZ_DISABLE_GMP_SANDBOX':'1'}
        cls.browser=cls.driver.firefox.launch(**options)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.driver.stop()
    def setUp(self):
        self.context=self.browser.new_context();self.addCleanup(self.context.close)
        self.context.route('https://fixture.test/**',lambda route:route.fulfill(content_type='text/html',body='<label for="f">Frame name</label><input id="f"><a href="https://fixture.test/next">Frame link</a>' if route.request.url.endswith('/frame') else '<h1>Destination</h1>' if route.request.url.endswith('/next') else PAGE))
        self.page=self.context.new_page();self.page.set_default_timeout(5000);self.page.goto('https://fixture.test/')
        self.backend=BrowserBackend(tempfile.gettempdir());self.backend.page=self.page;self.backend.context=self.context;self.backend.start=lambda:None
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.session=WebSession(self.temp.name,self.backend);self.session._refresh()
    def element(self,label):return next(e for e in self.session.snapshot['elements'] if e['label']==label)
    def test_readable_semantics_document_order_and_hidden_content(self):
        snapshot=self.session.snapshot;self.assertNotIn('window.sent',snapshot['text']);self.assertNotIn('Hidden link',snapshot['text'])
        self.assertLess(self.element('Fixture heading')['start'],self.element('Next page')['start'])
        self.assertEqual(self.element('Submit application')['role'],'button');self.assertEqual(self.element('Shadow action')['role'],'button')
        self.assertTrue(any(e['role']=='table' for e in snapshot['elements']))
    def test_voice_uses_actual_controls_and_table_cursor(self):
        self.assertIn('Agree',self.session.command('next checkbox'))
        self.assertIn('checked',self.session.command('check checkbox'))
        self.assertIn('not checked',self.session.command('uncheck checkbox'))
        self.assertIn('Choice',self.session.command('next combo box'))
        self.assertIn('Two',self.session.command('select option Two'))
        self.assertIn('table',self.session.command('next table'))
        self.assertIn('Apple',self.session.command('next row'))
        self.assertIn('One dollar',self.session.command('next column'))
        self.assertIn('Summary',self.session.command('last row'))
    def test_hidden_frame_and_aria_control_are_handled(self):
        self.page.locator('iframe').evaluate("e=>e.style.display='none'")
        self.page.evaluate("()=>{let e=document.createElement('div');e.textContent='Custom check';e.setAttribute('role','checkbox');e.setAttribute('aria-label','Custom check');e.setAttribute('aria-checked','false');e.onclick=()=>e.setAttribute('aria-checked',e.getAttribute('aria-checked')==='true'?'false':'true');document.body.append(e);}")
        self.session._refresh()
        self.assertFalse(any(e.get('frame_index') for e in self.session.snapshot['elements']))
        item=self.element('Custom check');self.backend.choose_form_option(item,'yes')
        self.assertEqual(self.page.locator('[role=checkbox]').get_attribute('aria-checked'),'true')
    def test_hidden_rows_and_rich_edit_children_do_not_create_extra_stops(self):
        self.page.locator('table').evaluate("e=>{let r=e.insertRow(1);r.style.display='none';r.insertCell().textContent='Hidden row';}")
        self.page.evaluate("()=>{let e=document.createElement('div');e.contentEditable='true';e.setAttribute('aria-label','Rich edit');e.innerHTML='<p>First rich paragraph</p><p>Second rich paragraph</p>';document.body.append(e);}")
        self.session._refresh()
        self.assertNotIn('Hidden row',self.session.snapshot['text'])
        self.session.command('next table');self.assertIn('Apple',self.session.command('next row'))
        from web_browse import kind
        rich=[e for e in self.session.snapshot['elements'] if e.get('editable') and kind(e)=='edit']
        self.assertEqual([e['label'] for e in rich],['Rich edit'])
    def test_frames_fill_and_check_actual_target(self):
        item=self.element('Frame name');self.backend.fill(item,'Frame value')
        self.assertEqual(self.page.frames[1].locator('#f').input_value(),'Frame value');self.assertEqual(self.page.locator('#name').input_value(),'')
    def test_shadow_accessible_name_and_activation(self):
        item=self.element('Shadow action');self.backend.focus(item);self.backend.activate(item)
        self.assertEqual(self.page.locator('#shadow button').count(),1)
    def test_private_fill_never_exposes_password_in_snapshot(self):
        item=self.element('Password')
        with self.assertRaises(WebError):self.backend.fill(item,'private')
        self.backend.fill(item,'private',private=True)
        snapshot=self.backend.snapshot();self.assertNotIn('private',snapshot['text']);self.assertEqual(next(c for c in snapshot['controls'] if c['type']=='password')['value'],'')
    def test_stale_label_cannot_fill_another_control(self):
        item=self.element('Name');self.page.locator('label[for=name]').evaluate("e=>e.textContent='Different'")
        with self.assertRaises(WebError):self.backend.fill(item,'wrong')
        self.assertEqual(self.page.locator('#name').input_value(),'')
    def test_checkbox_combo_and_table_navigation(self):
        cursor=self.session.browse;self.assertIn('Agree',cursor.move('x'));self.assertIn('checked',self.session.keyboard('Space'))
        self.assertTrue(self.page.locator('#agree').is_checked());self.assertIn('Choice',self.session.keyboard('Quick:c'));self.session.keyboard('Down');self.assertEqual(self.page.locator('select').input_value(),'Two')
        self.session.keyboard('Quick:t');self.session.keyboard('Table:Home');self.assertIn('Apple',self.session.keyboard('Table:Down'));self.assertIn('One dollar',self.session.keyboard('Table:Right'));self.assertIn('Summary',self.session.keyboard('Table:Down'))
    def test_keyboard_field_commit_cancel_and_no_auto_submit(self):
        self.session.keyboard('Quick:e');self.session.keyboard('Activate');self.session.keyboard_field['value']='Typed';self.session.keyboard('Tab')
        self.assertEqual(self.page.locator('#name').input_value(),'Typed');self.assertEqual(self.page.evaluate('window.sent'),0)
    def test_required_radio_group_is_satisfied_and_disabled_field_ignored(self):
        self.page.locator('#name').fill('Tester');self.page.locator('iframe').evaluate('e=>e.remove()');self.session._refresh()
        response=self.session.prepare_submit();self.assertNotIn('Required fields appear empty',response);self.assertNotIn('Unavailable',response);self.assertEqual(self.page.evaluate('window.sent'),0)
    def test_link_confirmation_navigates_without_unreviewed_actions(self):
        self.session.keyboard('Quick:k');self.assertIn('Say yes or no',self.session.keyboard('Activate'));self.assertEqual(self.page.url,'https://fixture.test/')
        result=self.session.command('yes');self.assertIn('Destination',result);self.assertEqual(self.page.url,'https://fixture.test/next')
if __name__=='__main__':unittest.main()

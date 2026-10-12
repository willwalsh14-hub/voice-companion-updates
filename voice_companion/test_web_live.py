"""Real Firefox DOM tests using synthetic HTTPS pages; no accounts or real submissions."""
import os,sys,tempfile,unittest,time,threading
from unittest.mock import Mock,patch
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
        self.context=getattr(self,'persistent_context',None) or self.browser.new_context()
        if hasattr(self,'persistent_context'):self.addCleanup(lambda:self.context.unroute_all(behavior='wait'))
        else:self.addCleanup(self.context.close)
        self.context.route('https://fixture.test/**',lambda route:route.fulfill(content_type='text/html',body='<label for="f">Frame name</label><input id="f"><a href="https://fixture.test/next">Frame link</a>' if route.request.url.endswith('/frame') else '<h1>Destination</h1>' if route.request.url.endswith('/next') else PAGE))
        self.page=self.context.new_page();self.page.set_default_timeout(5000);self.page.goto('https://fixture.test/')
        if hasattr(self,'persistent_context'):self.addCleanup(self.page.close)
        self.backend=BrowserBackend(tempfile.gettempdir());self.backend.page=self.page;self.backend.context=self.context;self.backend.start=lambda:None
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.session=WebSession(self.temp.name,self.backend);self.session._refresh()
    def element(self,label):return next(e for e in self.session.snapshot['elements'] if e['label']==label)
    def test_readable_semantics_document_order_and_hidden_content(self):
        snapshot=self.session.snapshot;self.assertNotIn('NameName',snapshot['text']);self.assertNotIn('AgreeAgree',snapshot['text']);self.assertNotIn('window.sent',snapshot['text']);self.assertNotIn('Hidden link',snapshot['text'])
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
    def test_long_fields_and_unchanged_rich_text_are_preserved(self):
        original='Long value. '*300
        self.page.locator('#name').fill(original);self.session._refresh()
        self.session.command('next edit field');self.session.command('edit current field')
        self.assertEqual(self.session.keyboard_field['value'],original)
        self.session.command('save field');self.assertEqual(self.page.locator('#name').input_value(),original)
        self.page.evaluate("()=>{let e=document.createElement('div');e.contentEditable='true';e.setAttribute('aria-label','Rich editor');e.id='rich';e.innerHTML='<p><b>Original bold</b></p><p>Second paragraph</p>';document.body.append(e);}")
        self.session._refresh();item=self.element('Rich editor');self.session.browse.index=self.session.browse.elements.index(item)
        original_html=self.page.locator('#rich').inner_html()
        self.session.command('edit current field');self.assertIn('Original bold',self.session.keyboard_field['value'])
        self.session.command('save field');self.assertEqual(self.page.locator('#rich').inner_html(),original_html)
    def test_voice_line_break_changes_staged_multiline_value(self):
        self.session._refresh();item=self.element('Notes');self.session.browse.index=self.session.browse.elements.index(item)
        self.session.command('edit current field');self.session.command('type First')
        self.assertEqual(self.session.command('new line'),'New line.');self.session.command('save field')
        self.assertEqual(self.page.locator('textarea').input_value(),'First\n')
    def test_unicode_element_offsets_match_the_actual_reading_text(self):
        self.page.evaluate("()=>{let e=document.createElement('p');e.textContent='Before heading: \U0001f600 café';document.body.prepend(e);}")
        self.session._refresh();text=self.session.snapshot['text']
        for label in ('Fixture heading','Next page','Name','Apple','Frame name','Shadow action'):
            item=self.element(label);self.assertIn(label,text[item['start']:item['end']])
        self.session.command('next heading');self.assertEqual(text[self.session.reading.position:self.session.reading.position+15],'Fixture heading')
    def test_keyboard_and_voice_read_the_same_complete_html_paragraph(self):
        paragraph='A complete paragraph. '*100
        self.page.locator('p').first.evaluate('(e,text)=>e.textContent=text',paragraph)
        self.session._refresh();voice=self.session.command('next paragraph')
        self.session._refresh();keyboard=self.session.keyboard('Quick:p')
        self.assertEqual(voice,paragraph.strip());self.assertEqual(keyboard,voice)
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
        self.session.keyboard('Quick:k');result=self.session.keyboard('Activate');self.assertIn('Destination',result);self.assertEqual(self.page.url,'https://fixture.test/next');self.assertIsNone(self.session.pending)
    def test_virtual_view_traverses_display_contents_and_labels_unnamed_links(self):
        self.page.set_content('<body style="display:contents"><main style="display:contents"><h1>Search results</h1><p>Result description</p><a href="https://fixture.test/next"><img alt="Result link"></a></main><div style="display:none">Hidden text</div><iframe style="display:none" src="https://captcha.test/"></iframe></body>')
        result=self.session._refresh()
        self.assertIn('Search results',self.session.reading.text);self.assertIn('Result link',self.session.reading.text)
        self.assertNotIn('Hidden text',self.session.reading.text);self.assertFalse(self.session.snapshot['challenge'])
        self.assertIn('Virtual page view',result);self.assertIn('heading',self.session.keyboard('Quick:h'))
        self.assertIn('Result link, link',self.session.keyboard('Quick:k'));self.assertEqual(self.session.browse.current()['href'],'https://fixture.test/next')

    @unittest.skipUnless(sys.platform=='win32','Windows global Control key integration')
    def test_control_silences_while_browser_has_focus_in_awake_and_sleep_states(self):
        import ctypes
        import companion as app
        from keyboard_speech import KeyboardSpeech
        voice=Mock(WaitUntilDone=Mock(return_value=True))
        with patch('win32com.client.Dispatch',return_value=voice):
            worker=KeyboardSpeech();worker.configure('',0,100)
            try:
                self.page.bring_to_front()
                for mode in ('awake','sleep'):
                    with patch.object(app,'SLEEP_RETURN_MODE','web'):
                        worker.speak('Speech in '+mode);time.sleep(.05);worker.narration_interrupt.clear()
                        ctypes.windll.user32.keybd_event(0x11,0,0,0)
                        try:self.assertTrue(worker.narration_interrupt.wait(1))
                        finally:ctypes.windll.user32.keybd_event(0x11,0,2,0)
                        time.sleep(.05)
                        self.assertTrue(any(call.args==('',3) for call in voice.Speak.call_args_list))
            finally:worker.close();worker.thread.join(2)

    def test_link_and_heading_lists_browse_without_confirmation_and_return_to_page(self):
        self.assertIn('Links list',self.session.list_links());self.assertIsNone(self.session.pending)
        self.session.command('next');self.assertIsNone(self.session.pending)
        self.assertIn('Virtual page view',self.session.command('cancel'));self.assertIsNone(self.session.list_focus)
        self.assertIn('Headings list',self.session.command('list headings'))
        self.assertIn('heading',self.session.command('open current heading'));self.assertIsNone(self.session.list_focus)
        self.assertEqual(self.session.reading.position,self.session.heading_items()[0]['start'])

    @unittest.skipUnless(sys.platform=='win32','Windows application focus and keyboard integration')
    def test_load_speaks_restores_reading_focus_and_real_keys_work(self):
        import companion as app
        from app_window import CompanionWindow
        window=CompanionWindow('live browser test');window.start()
        def ui(function):
            done=threading.Event();errors=[]
            def run():
                try:function()
                except Exception as exc:errors.append(exc)
                finally:done.set()
            window.ui_actions.put(('ui_call',run));self.assertTrue(done.wait(5))
            if errors:raise errors[0]
        def wait(condition):
            deadline=time.monotonic()+5
            while not condition() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(condition())
        output=Mock(problem=None);window.keyboard_output=output
        try:
            with patch.object(app,'APP_WINDOW',window),patch.object(app,'web_session',self.session),patch.object(app,'KEYBOARD_SPEECH',output),patch.object(app,'voice',Mock(),create=True),patch.object(app,'TEXT_MODE',False),patch.object(app,'KEYBOARD_BATCH',False),patch.object(app,'UI_CONTEXT_CACHE',None),patch.object(app,'interrupt_speech'),patch.object(app,'update_audio_ducking'),patch.object(app,'pending_website',None):
                app.sync_app_context('web',True);wait(lambda:window.context.get('web_revision')==self.session.page_revision)
                self.page.bring_to_front()
                self.assertEqual(app.handle_keyboard('web key Refresh','web'),'web')
                wait(lambda:window.context.get('web_revision')==self.session.page_revision)
                ui(lambda:self.assertIs(window.editor.focus_get(),window.editor))
                import ctypes
                ui(lambda:self.assertEqual(ctypes.windll.user32.GetForegroundWindow(),ctypes.windll.user32.GetAncestor(window.root.winfo_id(),2)))
                self.assertTrue(any('Page: Fixture' in str(call.args[0]) for call in output.speak.call_args_list))
                output.reset_mock()
                ctypes.windll.user32.keybd_event(ord('H'),0,0,0);ctypes.windll.user32.keybd_event(ord('H'),0,2,0)
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.session.browse.current()['label'],'Fixture heading')
                self.assertTrue(any('heading' in str(call.args[0]) for call in output.speak.call_args_list))
                ctypes.windll.user32.keybd_event(0x28,0,0,0);ctypes.windll.user32.keybd_event(0x28,0,2,0)
                wait(lambda:any('Readable text' in str(call.args[0]) for call in output.speak.call_args_list))
                ui(lambda:window.editor.event_generate('<KeyPress>',keysym='F7'))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.session.list_focus,'links');self.assertIsNone(self.session.pending)
                wait(lambda:window.context.get('menu_letters'));self.assertNotIn('confirmation',window.context)
                ui(lambda:window.typed.event_generate('<KeyPress>',keysym='F6'))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.session.list_focus,'headings')
                ui(lambda:window.typed.event_generate('<KeyPress>',keysym='Return'))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertIsNone(self.session.list_focus);wait(lambda:window.context.get('source') is not None)
                ui(lambda:window.editor.event_generate('<KeyPress>',keysym='l',state=4))
                wait(lambda:any(item==('keyboard','Web:Search') for item in window.commands.queue))
        finally:
            ui(window.root.destroy);window.thread.join(5)

@unittest.skipUnless(sys.platform=='win32','Installed Windows Chrome browser')
class ChromeLiveTests(LiveWebTests):
    browser_name='chrome'
    @classmethod
    def setUpClass(cls):
        cls.profile=tempfile.TemporaryDirectory()
        cls.adapter=BrowserBackend(cls.profile.name,cls.browser_name)
        try:cls.adapter.start()
        except WebError:
            cls.profile.cleanup()
            if os.getenv('VOICE_COMPANION_REQUIRE_BROWSER_CHANNELS'):raise
            raise unittest.SkipTest(cls.browser_name+' is not installed.')
        cls.persistent_context=cls.adapter.context
    @classmethod
    def tearDownClass(cls):cls.adapter.close();cls.profile.cleanup()

@unittest.skipUnless(sys.platform=='win32','Installed Windows Edge browser')
class EdgeLiveTests(ChromeLiveTests):browser_name='edge'

@unittest.skipUnless(sys.platform=='win32','Installed Windows Brave browser')
class BraveLiveTests(ChromeLiveTests):browser_name='brave'
if __name__=='__main__':unittest.main()

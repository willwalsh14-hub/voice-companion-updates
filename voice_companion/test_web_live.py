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
        self.backend.fill(item,'private')
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
        self.context.route('https://captcha.test/**',lambda r:r.fulfill(content_type='text/html',body='<p>Hidden frame</p>'))
        self.page.set_content('<body style="display:contents"><main style="display:contents"><h1>Search results</h1><p>Result description</p><a href="https://fixture.test/next"><img alt="Result link"></a></main><div style="display:none">Hidden text</div><iframe style="display:none" src="https://captcha.test/"></iframe></body>')
        result=self.session._refresh()
        self.assertIn('Search results',self.session.reading.text);self.assertIn('Result link',self.session.reading.text)
        self.assertNotIn('Hidden text',self.session.reading.text);self.assertFalse(self.session.snapshot['challenge'])
        self.assertIn('Page loaded',result);self.assertIn('heading',self.session.keyboard('Quick:h'))
        self.assertIn('Link Result link',self.session.keyboard('Quick:k'));self.assertEqual(self.session.browse.current()['href'],'https://fixture.test/next')

    def test_result_navigation_waits_for_delayed_single_page_content(self):
        self.page.set_content("""<title>Old</title><h1>Old page</h1><a href="https://fixture.test/next" onclick="event.preventDefault();history.pushState({},'','/next');document.body.setAttribute('aria-busy','true');setTimeout(()=>{document.title='New';document.body.innerHTML='<h1>New page</h1><p>Fresh content</p>';document.body.removeAttribute('aria-busy');},700)">Search result</a>""")
        self.session._refresh();self.session.keyboard('Quick:k');result=self.session.keyboard('Activate')
        self.assertIn('Page loaded: New',result);self.assertIn('Fresh content',self.session.reading.text);self.assertNotIn('Old page',self.session.reading.text)

    def test_new_tab_result_is_loaded_before_virtual_view_capture(self):
        self.page.set_content("""<a target="_blank" href="https://fixture.test/next">New tab result</a>""")
        self.session._refresh();self.session.keyboard('Quick:k');result=self.session.keyboard('Activate')
        self.addCleanup(self.backend.page.close)
        self.assertIsNot(self.backend.page,self.page);self.assertIn('Destination',self.session.reading.text);self.assertIn('Page loaded:',result)

    def test_native_fields_choices_and_listboxes_need_no_activation(self):
        self.page.set_content("""<h1>Form</h1><p>Before</p><form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1"><input aria-label="Name"><select aria-label="Choice"><option>One</option><option>Two</option><option disabled>Three</option></select><select size="4" aria-label="List"><option>Alpha</option><option>Beta</option><option>Bravo</option></select></form><p>After</p>""")
        self.session._refresh();self.session.keyboard('Tab');self.assertEqual(self.session.keyboard_field['label'],'Name')
        self.session.command('type Typed name');self.session.keyboard('Tab');self.assertEqual(self.page.locator('input').input_value(),'Typed name')
        self.assertIn('Two.',self.session.keyboard('Down'));self.assertIn('One.',self.session.keyboard('ChoiceLetter:o'))
        self.session.keyboard('Tab');self.assertIn('Beta.',self.session.keyboard('ChoiceLetter:b'));self.assertIn('Bravo.',self.session.keyboard('ChoiceLetter:b'))
        self.assertIn('Alpha.',self.session.command('first option'));self.session.keyboard('Tab');self.assertIsNone(self.session.keyboard_field)
        self.assertEqual(self.page.evaluate('window.sent||0'),0)

    def test_choice_boundaries_do_not_fire_repeated_change_events(self):
        self.page.set_content('<select aria-label="Choice" onchange="window.changes++"><option>One</option><option>Two</option></select><script>window.changes=0</script>')
        self.session._refresh();self.session.keyboard('Quick:c')
        self.assertNotIn('of list',self.session.keyboard('End'));self.assertEqual(self.page.evaluate('window.changes'),1)
        self.assertIn('End of list',self.session.keyboard('Down'));self.session.keyboard('End')
        self.assertEqual(self.page.evaluate('window.changes'),1)
        self.assertNotIn('of list',self.session.keyboard('Home'));self.assertEqual(self.page.evaluate('window.changes'),2)
        self.assertIn('Start of list',self.session.keyboard('Up'));self.session.keyboard('Home')
        self.assertEqual(self.page.evaluate('window.changes'),2)

    def test_custom_combo_options_and_popup_are_one_tab_stop(self):
        self.page.set_content("""<button type="button" id="combo" role="combobox" aria-label="Custom choice" aria-controls="options" aria-expanded="false" onclick="this.setAttribute('aria-expanded','true');document.getElementById('options').style.display='block'" onblur="this.setAttribute('aria-expanded','false');document.getElementById('options').style.display='none'">Choose</button><div id="options" role="listbox" tabindex="-1" style="display:none"><div onmousedown="event.preventDefault()" role="option" aria-selected="true">Alpha</div><div onmousedown="event.preventDefault()" role="option">Beta</div><div onmousedown="event.preventDefault()" role="option">Bravo</div></div><input aria-label="Next"><script>document.querySelectorAll('[role=option]').forEach(option=>option.onclick=()=>{document.querySelectorAll('[role=option]').forEach(o=>o.setAttribute('aria-selected',String(o===option)));document.getElementById('combo').setAttribute('aria-valuetext',option.textContent);});</script>""")
        self.session._refresh();self.session.keyboard('Tab');self.assertEqual(self.session.keyboard_field['label'],'Custom choice')
        self.assertIn('Beta.',self.session.keyboard('ChoiceLetter:b'));self.assertIn('Bravo.',self.session.keyboard('Down'))
        self.session.keyboard('Tab');self.assertEqual(self.session.keyboard_field['label'],'Next')

    def test_later_dom_updates_refresh_buffer_without_read_page(self):
        self.session.auto_read=False;self.session._refresh();previous=self.session.page_revision
        self.page.evaluate("()=>{const p=document.createElement('p');p.textContent='Late content';document.body.append(p)}")
        self.page.wait_for_timeout(20);self.session.refresh_changed()
        self.assertIn('Late content',self.session.reading.text);self.assertGreater(self.session.page_revision,previous)
        self.page.evaluate("()=>document.title='Updated title'");self.page.wait_for_timeout(20)
        self.assertIn('Page loaded: Updated title',self.session.refresh_changed())

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
                self.assertTrue(any('Page loaded: Fixture' in str(call.args[0]) for call in output.speak.call_args_list))
                output.reset_mock()
                ctypes.windll.user32.keybd_event(ord('H'),0,0,0);ctypes.windll.user32.keybd_event(ord('H'),0,2,0)
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.session.browse.current()['label'],'Fixture heading')
                self.assertTrue(any('heading' in str(call.args[0]) for call in output.speak.call_args_list))
                ctypes.windll.user32.keybd_event(0x28,0,0,0);ctypes.windll.user32.keybd_event(0x28,0,2,0)
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                wait(lambda:any('Readable text' in str(call.args[0]) for call in output.speak.call_args_list))
                self.page.locator('#title').evaluate("e=>e.insertAdjacentHTML('afterend','<p>Before later heading</p><h2>Later heading destination</h2><p>After later heading</p>')")
                self.session._refresh();app.sync_app_context('web',True)
                wait(lambda:window.context.get('web_revision')==self.session.page_revision)
                for key,state,expected in [('h',0,'Fixture heading'),('h',0,'Later heading destination'),('Down',0,'After later heading'),('Up',0,'Later heading destination'),('h',1,'Fixture heading')]:
                    ui(lambda k=key,s=state:window.editor.event_generate('<KeyPress>',keysym=k,state=s))
                    wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                    wait(lambda:window.context.get('caret')==self.session.reading.position)
                    position=self.session.reading.position
                    ui(lambda p=position:self.assertEqual(len(window.editor.get('1.0','insert')),p))
                    self.assertTrue(any(expected in str(call.args[0]) for call in output.speak.call_args_list))
                ui(lambda:(window.editor.event_generate('<KeyPress>',keysym='h'),window.editor.event_generate('<KeyPress>',keysym='Down')))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                wait(lambda:window.context.get('caret')==self.session.reading.position)
                position=self.session.reading.position;self.assertIn('After later heading',self.session.reading.text[position:self.session.reading.text.find('\n',position) if '\n' in self.session.reading.text[position:] else len(self.session.reading.text)])
                ui(lambda p=position:self.assertEqual(len(window.editor.get('1.0','insert')),p))
                ui(lambda:window.editor.event_generate('<KeyPress>',keysym='F7'))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.session.list_focus,'links');self.assertIsNone(self.session.pending)
                wait(lambda:window.context.get('source') is None)
                self.assertFalse(window.context.get('menu_letters'));self.assertNotIn('confirmation',window.context)
                ui(lambda:window.typed.event_generate('<KeyPress>',keysym='F6'))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.session.list_focus,'headings')
                ui(lambda:window.typed.event_generate('<KeyPress>',keysym='Return'))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertIsNone(self.session.list_focus);wait(lambda:window.context.get('source') is not None)
                ui(lambda:window.editor.event_generate('<KeyPress>',keysym='End',state=4))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.session.reading.position,len(self.session.reading.text))
                ui(lambda:window.editor.event_generate('<KeyPress>',keysym='Home',state=4))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web');self.assertEqual(self.session.reading.position,0)
                for _ in range(2):
                    ui(lambda:window.editor.event_generate('<KeyPress>',keysym='Tab'))
                    wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.session.keyboard_field['label'],'Name')
                wait(lambda:window.context.get('web_field') is not None)
                ui(lambda:window.editor.insert('1.0','From keys'))
                wait(lambda:any(isinstance(item,tuple) and item[0]=='text_edit' for item in window.commands.queue));app.process_keyboard_batch('web')
                ui(lambda:window.editor.event_generate('<KeyPress>',keysym='Tab'))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web');self.assertEqual(self.page.locator('#name').input_value(),'From keys')
                wait(lambda:window.context.get('web_private'))
                ui(lambda:window.typed.insert(0,'private value'))
                ui(lambda:window.typed.event_generate('<KeyPress>',keysym='Home',state=4))
                wait(lambda:not window.commands.empty());app.process_keyboard_batch('web')
                self.assertEqual(self.page.locator('#secret').input_value(),'private value');self.assertEqual(self.session.reading.position,0)
                wait(lambda:window.context.get('source') is not None and not window.context.get('web_private'))
                ui(lambda:window.editor.event_generate('<KeyPress>',keysym='l',state=4))
                wait(lambda:any(item==('keyboard','Web:Search') for item in window.commands.queue))
                self.assertEqual(app.process_keyboard_batch('web'),'search');wait(lambda:window.context.get('mode')=='search')
                ui(lambda:(window.typed.delete(0,'end'),window.typed.insert(0,'fixture.test/next'),window.typed.event_generate('<KeyPress>',keysym='Return')))
                wait(lambda:not window.commands.empty());self.assertEqual(app.process_keyboard_batch('search'),'web')
                self.assertEqual(self.session.snapshot['url'],'https://fixture.test/next');self.assertIn('Destination',self.session.reading.text)
        finally:
            ui(window.root.destroy);window.thread.join(5)

    def test_nested_search_headings_and_caret_ack_move_without_url_repetition(self):
        page='<h1>Search results</h1><h2><a href="https://fixture.test/next"><span>https://first.test/path</span><br><span class="result-title">First result title</span></a></h2><h2><a href="https://fixture.test/next"><span>second.test › article</span><br><span class="result-title">Second result title</span></a></h2>'
        self.page.set_content(page);self.session._refresh();self.session.keyboard('Quick:h');self.session.browse.set_position(self.session.reading.position)
        for key,expected in [('Quick:h','First result title'),('Quick:h','Second result title'),('Quick:Previous:h','First result title'),('Quick:Previous:h','Search results')]:
            message=self.session.keyboard(key);self.assertIn(expected,message);self.assertNotIn('https://',message)
            self.session.browse.set_position(self.session.reading.position)
            self.page.evaluate("()=>document.body.setAttribute('data-updated',String(Date.now()))")
            self.session.refresh_changed()
        self.assertIn('No previous heading',self.session.keyboard('Quick:Previous:h'))
        result_link=next(e for e in self.session.snapshot['elements'] if e['role']=='link')
        self.assertEqual(result_link['label'],'First result title')
        self.session.browse.index=self.session.browse.elements.index(result_link);self.assertIn('Destination',self.session.keyboard('Activate'))

    def test_leave_closes_all_owned_pages_and_keep_open_reuses_context(self):
        # Use an isolated context so closing it cannot affect another fixture.
        if self.page.context.browser:
            context=self.page.context.browser.new_context()
        else:
            options={'headless':True,'channel':{'chrome':'chrome','edge':'msedge'}.get(self.browser_name)}
            if self.browser_name=='brave':
                roots=[os.environ.get('PROGRAMFILES',''),os.environ.get('PROGRAMFILES(X86)',''),os.environ.get('LOCALAPPDATA','')]
                options={'headless':True,'executable_path':str(next(Path(root)/'BraveSoftware/Brave-Browser/Application/brave.exe' for root in roots if (Path(root)/'BraveSoftware/Brave-Browser/Application/brave.exe').is_file()))}
            browser=self.adapter.playwright.chromium.launch(**options);self.addCleanup(browser.close);context=browser.new_context()
        self.addCleanup(context.close)
        context.route('https://fixture.test/**',lambda r:r.fulfill(content_type='text/html',body='<title>Kept page</title><h1>Kept page</h1>'))
        owned=BrowserBackend(self.temp.name);owned.context=context;owned.page=context.new_page();owned.start=lambda:None
        session=WebSession(self.temp.name,owned);session.open('https://fixture.test');second=context.new_page();session.leave(True)
        self.assertIn('Kept page',session.resume());self.assertFalse(second.is_closed());pages=list(context.pages);session.leave(False)
        self.assertTrue(all(p.is_closed() for p in pages));self.assertIsNone(owned.context)

    def test_result_titles_and_virtual_cursor_stay_on_the_selected_text(self):
        self.page.set_content('<h1>Search</h1><p>Before result</p><a href="https://fixture.test/next"><h2 aria-label="https://source.test/path"><span class="source-title">https://source.test/path</span><br><span>Actual result title</span></h2></a><p>After result</p><h2>https://metadata.test/path</h2><h2>Last heading</h2><h2>Example.org accessibility</h2>')
        self.session._refresh();self.session.keyboard('Quick:h');message=self.session.keyboard('Quick:h')
        self.assertIn('Actual result title',message);self.assertNotIn('https://',message)
        self.assertTrue(any(e['label']=='Example.org accessibility' for e in self.session.heading_items()))
        position=self.session.reading.position;self.assertEqual(position,self.session.reading.text.index('Actual result title'))
        self.assertIn('After result',self.session.keyboard('Read:Down'));self.assertIn('Actual result title',self.session.keyboard('Read:Up'))
        self.assertIn('Last heading',self.session.keyboard('Quick:h'));self.assertIn('Actual result title',self.session.keyboard('Quick:Previous:h'))
        self.page.evaluate("()=>{let p=document.createElement('p');p.textContent='Inserted before everything';document.body.prepend(p);}")
        self.session.refresh_changed();self.assertEqual(self.session.reading.position,self.session.reading.text.index('Actual result title'))
        self.assertIn('Destination',self.session.keyboard('Activate'))


    def test_actual_brave_div_titles_are_unique_and_activate(self):
        # Shape observed on live Brave Search in Chrome: DIV title after site/cite.
        card='<a href="https://fixture.test/next"><div class="site-name-wrapper"><div class="site-name-content"><div>Example site</div><div class="url-wrapper"><cite class="snippet-url">fixture.test › home</cite></div></div></div><div class="title search-snippet-title line-clamp-1" title="Accessible result">Accessible result</div></a>'
        self.context.route('https://search.brave.com/**',lambda r:r.fulfill(content_type='text/html',body='<title>Search</title>'+card+card+'<a href="https://fixture.test/next">https://fixture.test/next</a><h6>Resources</h6>'))
        self.session.search('accessibility','brave')
        headings=self.session.heading_items();self.assertEqual(sum(e['label']=='Accessible result' for e in headings),1)
        result=self.session.keyboard('Quick:h');self.assertIn('Accessible result',result);self.assertNotIn('fixture.test',result)
        self.assertEqual(sum(e.get('label')=='Accessible result' for e in self.session.snapshot['controls']),1)
        self.assertIn('Destination',self.session.keyboard('Activate'))

    def test_delayed_popup_after_early_dom_change_refreshes_once(self):
        self.page.set_content("""<p id="status">Ready</p><a href="https://fixture.test/next" onclick="event.preventDefault();document.getElementById('status').textContent='Loading';setTimeout(()=>window.open(this.href),700)">Delayed result</a>""")
        self.session._refresh();self.session.keyboard('Quick:k');self.session.keyboard('Activate')
        self.addCleanup(self.backend.page.close)
        self.assertIsNot(self.backend.page,self.page);self.assertIn('Destination',self.session.reading.text)

    def test_fragment_navigation_and_frame_updates_preserve_cursor(self):
        self.page.set_content('<a href="#section">Jump</a><p>Before</p><div id="section"><h2>Target heading</h2><p>Target text</p></div><iframe src="https://fixture.test/frame"></iframe>')
        self.session._refresh();self.session.keyboard('Quick:k');self.session.keyboard('Activate')
        self.assertEqual(self.session.reading.position,self.session.reading.text.index('Target heading'))
        self.page.frames[1].evaluate("()=>document.body.prepend(Object.assign(document.createElement('p'),{textContent:'Fresh frame content'}))")
        self.page.wait_for_timeout(30);self.session.refresh_changed()
        self.assertIn('Fresh frame content',self.session.reading.text)
        self.assertEqual(self.session.reading.position,self.session.reading.text.index('Target heading'))

    def test_external_navigation_cancels_old_field(self):
        self.session.keyboard('Quick:f');self.assertIsNotNone(self.session.keyboard_field)
        self.page.goto('https://fixture.test/next');self.session.refresh_changed()
        self.assertIsNone(self.session.keyboard_field);self.assertIn('Destination',self.session.reading.text)

    def test_captcha_search_word_is_not_a_verification_page(self):
        self.context.route('https://www.google.com/**',lambda r:r.fulfill(content_type='text/html',body='<title>captcha help - Google Search</title><h3>Captcha accessibility help</h3><p>Ordinary search results.</p>' if '/sorry/' not in r.request.url else '<title>Google</title><p>Our systems have detected unusual traffic from your computer network.</p>'))
        self.session.search('captcha help','google');self.assertFalse(self.session.snapshot.get('challenge'))
        self.backend.goto('https://www.google.com/sorry/index');message=self.session._refresh()
        self.assertIn('human verification',message);self.assertEqual(self.session.list_focus,'search_engines')
        self.assertEqual(self.session.engine_choices[0],'brave')
        self.assertEqual(self.session.search_engine,'google')

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

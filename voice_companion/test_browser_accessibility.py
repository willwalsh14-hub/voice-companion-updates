"""Regression coverage for reported browser, editing and HTML-email failures."""
import queue,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import companion as app
from app_window import CompanionWindow,entry_feedback
from keyboard_text import typing_feedback,caret_feedback
from reading_navigation import ReadingCursor
from email_reader import render_html,spoken_range,message_controls
from mail_voice import MailSession
from test_web_assistant import FakeBackend
from web_assistant import WebSession,BrowserBackend

class BrowserAccessibilityTests(unittest.TestCase):
    def test_control_silences_all_engines_without_microphone_or_focus_dependency(self):
        import threading
        from keyboard_speech import KeyboardSpeech
        worker=KeyboardSpeech.__new__(KeyboardSpeech);worker.queue=queue.Queue();worker.narration_interrupt=threading.Event()
        engine=Mock();worker.settings=('',0,100,engine,0);worker.ai=Mock()
        worker.queue.put('obsolete speech');worker.poll_control(True)
        self.assertEqual(list(worker.queue.queue),[('interrupt',)]);self.assertTrue(worker.narration_interrupt.is_set())
        worker.ai.interrupt.assert_called_once();engine.interrupt.assert_called_once()
        worker.poll_control(True);engine.interrupt.assert_called_once()
        worker.poll_control(False);worker.poll_control(True);self.assertEqual(engine.interrupt.call_count,2)

    def test_web_confirmations_are_keyboard_accessible_even_when_in_a_list(self):
        session=Mock(pending={'role':'button'},submit_pending=None,list_focus='links')
        with patch.object(app,'web_session',session),patch.object(app,'pending_website',None),patch.object(app,'interrupt_speech'):
            self.assertEqual(app.app_context('web')['confirmation'],'yes')
            self.assertEqual(app.navigation_key('y','web'),'yes');self.assertEqual(app.navigation_key('n','web'),'no')
            self.assertTrue(app.web_feedback_control('yes','web'));self.assertFalse(app.web_feedback_control('yes','sleep'))

    def test_lists_have_shortcuts_and_do_not_create_pending_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend());session._refresh()
            with patch.object(app,'web_session',session),patch.object(app,'pending_website',None),patch.object(app,'interrupt_speech'),patch.object(app,'speak'):
                self.assertEqual(app.navigation_key('F7','web'),'list links');self.assertEqual(app.navigation_key('F6','web'),'list headings')
                session.list_links();self.assertNotIn('confirmation',app.app_context('web'))
                self.assertEqual(app.navigation_key('Enter','web'),'open current link')
                app.menu_state('web')[2](1);self.assertIsNone(session.pending)
                self.assertIn('Page loaded:',session.command('open current link'));self.assertEqual(session.backend.activated,['1'])

    def test_guided_web_yes_and_no_dispatch_through_app_keyboard_and_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend());session._refresh()
            with patch.object(app,'web_session',session),patch.object(app,'pending_website',None),patch.object(app,'APP_WINDOW',None),patch.object(app,'INPUT_MODE','mixed'),patch.object(app,'interrupt_speech'),patch.object(app,'speak'),patch.object(app,'speak_status'):
                session.command('find login')
                self.assertEqual(app.handle_keyboard(app.navigation_key('y','web'),'web'),'web')
                self.assertEqual(session.backend.activated,['0'])
                session.command('find login or transfer');app.handle('no','web')
                self.assertIn('Transfer',session.pending['label']);app.handle('yes','web')
                self.assertEqual(session.backend.activated,['0','1'])

    def test_empty_brave_results_never_redirect_to_another_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeBackend();backend.snapshot=lambda:{'url':backend.url,'title':'Search','text':'','controls':[]}
            session=WebSession(folder,backend);session.search('simple search',provider='brave')
            self.assertIn('search.brave.com',backend.url)

    def test_backspace_erased_character_and_delete_new_caret_character(self):
        for echo in ('characters','words','characters and words'):
            self.assertEqual(typing_feedback('abc','ac',1,echo,'BackSpace',2),'b')
            self.assertEqual(typing_feedback('abc','ac',1,echo,'Delete',1),'c')
            self.assertEqual(typing_feedback('abc','ab',2,echo,'Delete',2),'End of text.')
        self.assertEqual(entry_feedback('a b','ab',1,1,'Delete'),'b')
        self.assertEqual(typing_feedback('a b','ab',1,'characters','BackSpace',2),'space')
        window=CompanionWindow('test');window.feedback=Mock()
        window.report_entry_key('abc','ac',2,1,'BackSpace');window.feedback.assert_called_with('b')
        window.report_entry_key('abc','ac',1,1,'Delete');window.feedback.assert_called_with('c')
        window.context['web_private']=True
        window.report_entry_key('secret','secrt',5,4,'BackSpace');window.feedback.assert_called_with('Deleted.')
    def test_blank_is_consistent_for_keyboard_and_voice_reading(self):
        self.assertEqual(caret_feedback('First\n\nLast',6,'Down')[0],'Blank.')
        reader=ReadingCursor();reader.set_text('First\n\nLast');self.assertEqual(reader.move('line',1),'Blank.')
    def test_typed_search_can_start_from_main_menu_and_current_page(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend())
            with patch.object(app,'web_session',session),patch.object(app,'pending_website',None),patch.object(app,'APP_WINDOW',None),patch.object(app,'INPUT_MODE','mixed'),patch.object(app,'speak'),patch.object(session,'search',wraps=session.search) as search:
                self.assertEqual(app.handle_keyboard('search the web','awake'),'search')
                self.assertEqual(app.handle_keyboard('accessible donations','search'),'web')
                search.assert_called_once_with('accessible donations')
                for request in ('search the web','web key Search'):
                    self.assertEqual(app.handle_keyboard(request,'web'),'search')
                    self.assertEqual(app.handle_keyboard(app.navigation_key('Escape','search'),'search'),'web')
    def test_each_page_refresh_changes_revision_and_restores_focus(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend());session._refresh();old=session.page_revision
            with patch.object(app,'web_session',session),patch.object(app,'APP_WINDOW',None),patch.object(app,'pending_website',None),patch.object(app,'speak_status') as status,patch.object(app,'sync_app_context') as sync:
                self.assertEqual(app.handle('read page','web'),'web')
                self.assertGreater(session.page_revision,old)
                self.assertIn('Page loaded:',status.call_args.args[0]);self.assertIn(unittest.mock.call('web',True),sync.call_args_list)
                self.assertEqual(app.app_context('web')['web_revision'],session.page_revision)
    def test_search_entry_text_never_executes_a_matching_command(self):
        window=CompanionWindow('test');window.context['mode']='search';window.submit_entry('shut down computer')
        session=Mock(snapshot={'text':'Results'},page_revision=0);session.search.return_value='Results.'
        with patch.object(app,'APP_WINDOW',window),patch.object(app,'web_session',session),patch.object(app,'speak'),patch.object(app,'sync_app_context'),patch.object(app,'interrupt_speech'),patch.object(app,'KEYBOARD_BATCH',False),patch.object(app,'KEYBOARD_BATCH_TEXT',None),patch.object(app,'KEYBOARD_BATCH_STATUS',False):
            self.assertEqual(app.process_keyboard_batch('search'),'web');session.search.assert_called_once_with('shut down computer')
    def test_website_confirmation_supports_keyboard_without_a_page(self):
        with patch.object(app,'pending_website',('https://example.com','example.com')),patch.object(app,'interrupt_speech'),patch.object(app,'speak'),patch.object(app,'CONFIRM_CHOICE','yes'):
            self.assertEqual(app.app_context('awake')['confirmation'],'yes')
            self.assertEqual(app.navigation_key('y','awake'),'yes');self.assertEqual(app.navigation_key('n','awake'),'no')
            self.assertEqual(app.navigation_key('Escape','awake'),'no')
    def test_keyboard_search_keeps_microphone_sleep_state(self):
        window=CompanionWindow('test');window.context['mode']='search';window.submit_entry('weather')
        session=Mock(snapshot={'text':'Results'},page_revision=0);session.search.return_value='Results.'
        with patch.object(app,'APP_WINDOW',window),patch.object(app,'web_session',session),patch.object(app,'SLEEP_RETURN_MODE','search'),patch.object(app,'speak'),patch.object(app,'sync_app_context'),patch.object(app,'interrupt_speech'),patch.object(app,'KEYBOARD_BATCH',False),patch.object(app,'KEYBOARD_BATCH_TEXT',None),patch.object(app,'KEYBOARD_BATCH_STATUS',False):
            self.assertEqual(app.process_keyboard_batch('sleep'),'sleep');self.assertEqual(app.SLEEP_RETURN_MODE,'web');session.search.assert_called_once_with('weather')
    def test_load_status_survives_keyboard_batch_and_caret_acknowledgement(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend());session._refresh()
            window=Mock(commands=queue.Queue());window.commands.put('read page');window.commands.put(('text_edit',{}))
            with patch.object(app,'APP_WINDOW',window),patch.object(app,'web_session',session),patch.object(app,'pending_website',None),patch.object(app,'KEYBOARD_BATCH',False),patch.object(app,'KEYBOARD_BATCH_TEXT',None),patch.object(app,'KEYBOARD_BATCH_STATUS',False),patch.object(app,'speak_status') as status,patch.object(app,'sync_app_context'),patch.object(app,'apply_keyboard_edit'),patch.object(app,'interrupt_speech'):
                app.process_keyboard_batch('web');status.assert_called_once();self.assertIn('Page loaded:',status.call_args.args[0])
    def test_native_focus_does_not_replace_proxy_reading_speech(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend());session._refresh()
            with patch.object(app,'web_session',session),patch.object(app,'APP_WINDOW',Mock()),patch.object(app,'speech_busy',return_value=False),patch.object(session,'focus_notice') as notice:
                self.assertEqual(app.browser_focus_notice('web'),'');notice.assert_not_called()
    def test_supported_browser_adapters_choose_correct_engine(self):
        for browser in BrowserBackend.BROWSERS:
            driver=Mock();driver.chromium.launch_persistent_context.return_value.pages=[Mock()];driver.firefox.launch_persistent_context.return_value.pages=[Mock()]
            with tempfile.TemporaryDirectory() as folder,patch('playwright.sync_api.sync_playwright',return_value=Mock(start=Mock(return_value=driver))),patch.object(Path,'is_file',return_value=True):
                backend=BrowserBackend(folder,browser,headless=True);backend.start()
                engine=driver.firefox if browser=='firefox' else driver.chromium
                args=engine.launch_persistent_context.call_args.kwargs
                self.assertTrue(args['headless'])
                if browser in ('chrome','edge'):self.assertEqual(args['channel'],'chrome' if browser=='chrome' else 'msedge')
                if browser=='brave':self.assertTrue(args['executable_path'].endswith('brave.exe'))

class EmailControlTests(unittest.TestCase):
    def session(self,body):
        session=MailSession('gmail','unused');session.view='message';session.body_content=body;session.set_format('html');return session
    def test_image_donation_link_and_aria_button_are_read_as_controls(self):
        body=render_html('<p>Help us.</p><a href="https://example.com/donate"><img src="remote.png" alt="Click to donate"></a><p><a role="button" href="https://example.com/give">Donate now</a></p>')
        self.assertEqual([body[a:b] for a,b,s,u in message_controls(body)],['Click to donate','Donate now'])
        speech=spoken_range(body,0,len(body));self.assertIn('Click to donate, link',speech);self.assertIn('Donate now, button',speech)
        session=self.session(body);self.assertIn('Click to donate, link',session._next_body())
        session.reading.position=0
        self.assertIn('Click to donate, link',session.process('next link'))
        self.assertIn('Donate now, button',session.process('next button'))
        with patch('webbrowser.open') as opener:
            self.assertIn('Opening',session.process('activate current button'));opener.assert_called_once_with('https://example.com/give')
        self.assertIn('Click to donate, link',session.process('previous link'))
    def test_empty_image_anchor_has_label_and_button_without_url_is_passive(self):
        body=render_html('<a href="https://example.com/donate" aria-label="Donate"><img src="background.png"></a><button>Click to donate</button>')
        self.assertTrue(body.startswith('Donate'));self.assertEqual(len(message_controls(body)),2)
        session=self.session(body);self.assertIn('Click to donate, button',session.process('next button'))
        with patch('webbrowser.open') as opener:
            self.assertIn('no safe link',session.process('activate current button'));opener.assert_not_called()
    def test_outlook_vml_and_nested_buttons_keep_a_single_usable_stop(self):
        body=render_html('<v:roundrect href="https://example.com/donate"><center>Click to donate</center></v:roundrect><a href="https://example.com/give"><button>Give</button></a>')
        controls=message_controls(body)
        self.assertEqual([(body[a:b],s,u) for a,b,s,u in controls],[('Click to donate','button','https://example.com/donate'),('Give','button','https://example.com/give')])
    def test_voice_line_reports_role_without_changing_copyable_body(self):
        body=render_html('<p>First</p><p><a href="https://example.com">Donate</a></p>');session=self.session(body)
        self.assertEqual(session.process('next line'),'Donate, link');self.assertEqual(str(session.body_text),'First\nDonate')
        self.assertEqual(app.navigation_key('Email:next link','mailbox'),'next link')
    def test_plain_text_does_not_enable_html_controls(self):
        session=self.session(render_html('<a href="https://example.com">Donate</a>'));session.set_format('plain text')
        self.assertIn('Choose HTML',session.process('next link'))

if __name__=='__main__':unittest.main()


class FormPolishBackend(FakeBackend):
    def __init__(self):
        super().__init__()
        self.controls=[
            {'key':'name','role':'field','label':'Name','tag':'input','type':'text','value':''},
            {'key':'choice','role':'field','label':'Choice','tag':'select','type':'select-one','options':[{'label':'One','value':'1'},{'label':'Two','value':'2'},{'label':'Three','value':'3','disabled':True}],'selected_label':'One'},
            {'key':'list','role':'field','label':'List','tag':'select','type':'select-one','size':4,'options':[{'label':'Alpha','value':'a'},{'label':'Beta','value':'b'},{'label':'Bravo','value':'c'}],'selected_label':'Alpha'},
        ]
    def snapshot(self):
        elements=[];lines=['Before'];position=len('Before')+1
        elements.append({'key':'before','role':'paragraph','label':'Before','start':0,'end':6})
        for control in self.controls:
            line=control['label']+' '+(control.get('value') or control.get('selected_label',''))
            elements.append(dict(control,start=position,end=position+len(line)))
            lines.append(line);position+=len(line)+1
        elements.append({'key':'after','role':'paragraph','label':'After','start':position,'end':position+5});lines.append('After')
        return {'url':self.url,'title':'Form','text':'\n'.join(lines),'elements':elements,'controls':[dict(control) for control in self.controls]}

class WebPolishTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.backend=FormPolishBackend();self.session=WebSession(self.folder.name,self.backend);self.session._refresh()
    def test_fields_automatically_accept_typing_and_tab_or_arrows_leave(self):
        self.session.keyboard('Tab');self.assertEqual(self.session.keyboard_field['key'],'name')
        self.session.command('type Hello');self.session.keyboard('Tab')
        self.assertEqual(self.backend.filled,[('name','Hello')]);self.assertEqual(self.session.keyboard_field['key'],'choice')
        self.session.keyboard('Tab');self.assertEqual(self.session.keyboard_field['key'],'list')
        self.session.keyboard('Tab');self.assertIsNone(self.session.keyboard_field)
        self.session.keyboard('PageTop');self.session.keyboard('Quick:e');self.session.command('type Saved')
        self.session.keyboard('Leave:Up');self.assertIsNone(self.session.keyboard_field);self.assertEqual(self.backend.filled[-1],('name','Saved'))
    def test_combo_and_listbox_arrows_home_end_letters_and_voice(self):
        self.session.keyboard('Quick:c');self.session.keyboard('Activate');self.assertIsNotNone(self.session.keyboard_field)
        self.assertEqual(self.session.keyboard('Down'),'Two.');self.assertEqual(self.session.keyboard('End'),'End of list.')
        self.assertEqual(self.session.keyboard('Home'),'One.');self.assertEqual(self.session.keyboard('ChoiceLetter:t'),'Two.')
        self.session.keyboard('Quick:s');self.session.keyboard('Activate');self.assertEqual(self.session.keyboard_field['key'],'list')
        self.assertEqual(self.session.keyboard('ChoiceLetter:b'),'Beta.');self.assertEqual(self.session.keyboard('ChoiceLetter:b'),'Bravo.')
        self.assertEqual(self.session.command('first option'),'Alpha.');self.assertEqual(self.session.command('last option'),'Bravo.')
        self.assertEqual(self.session.command('select option Beta'),'Beta.')
        self.assertEqual(self.session.command('choose option beginning with b'),'Bravo.')
    def test_page_boundaries_all_voice_aliases_and_keyboard_are_exact(self):
        for request in ('top of page','beginning of page','start of page','go to the top of the page','move to beginning of page'):
            self.session.command(request);self.assertEqual(self.session.reading.position,0)
        for request in ('bottom of page','end of page','go to bottom of page','move to end of page'):
            self.session.command(request);self.assertEqual(self.session.reading.position,len(self.session.reading.text))
        self.session.keyboard('PageTop');self.assertEqual(self.session.reading.position,0)
        self.session.keyboard('PageBottom');self.assertEqual(self.session.reading.position,len(self.session.reading.text))
    def test_auto_read_setting_keeps_loaded_message_and_explicit_read(self):
        self.session.auto_read=False;notice=self.session._refresh()
        self.assertIn('Page loaded:',notice);self.assertNotIn('Before',notice)
        self.assertIn('Before',self.session.reading.text);self.assertIn('Before',self.session.command('read page'))
        from settings_model import SettingsSession,fields
        settings=SettingsSession({},{});self.assertTrue(settings.values['web_auto_read'])
        settings.set('web_auto_read','off');self.assertFalse(settings.values['web_auto_read'])
        self.assertTrue(any(field.key=='web_auto_read' and field.kind=='check' for field in fields('Web browser',{})))
        with patch.object(app,'PREFERENCES',{}),patch.object(app,'web_session',self.session),patch.object(app,'save_preferences'),patch.object(app,'speak'),patch.object(app,'APP_WINDOW',None):
            app.handle('automatic page reading off','web');self.assertFalse(app.PREFERENCES['web_auto_read'])
            app.handle('enable automatic page reading','web');self.assertTrue(app.PREFERENCES['web_auto_read'])
    def test_escape_from_page_exits_and_field_escape_can_return_then_exit(self):
        with patch.object(app,'web_session',self.session),patch.object(app,'pending_website',None),patch.object(app,'speak'),patch.object(app,'APP_WINDOW',None),patch.object(app,'interrupt_speech'):
            self.assertEqual(app.handle_keyboard(app.navigation_key('Web:Escape','web'),'web'),'awake')
            self.session.keyboard('Quick:e');self.session.keyboard('Activate');self.assertEqual(app.navigation_key('Web:Escape','web'),'web key Escape')
            self.session.keyboard('Escape');self.assertEqual(app.navigation_key('Web:Escape','web'),'leave website')
    def test_arrowing_into_field_keeps_virtual_cursor_readonly_until_explicit_edit(self):
        position=next(item['start'] for item in self.session.browse.elements if item['key']=='name')
        with patch.object(app,'web_session',self.session),patch.object(app,'pending_website',None),patch.object(app,'sync_app_context'):
            context=app.app_context('web')
            app.apply_keyboard_edit({'source':context['source'],'before':context['text'],'after':context['text'],'caret':position,'readonly':True,'selection':None,'seq':1},'web')
            self.assertIsNone(self.session.keyboard_field);self.assertTrue(app.app_context('web')['readonly'])
            self.session.keyboard('Quick:e');self.assertIsNone(self.session.keyboard_field)
            self.session.keyboard('Activate');self.assertEqual(self.session.keyboard_field['key'],'name');self.assertFalse(app.app_context('web')['readonly'])
    def test_link_role_is_first_and_main_menu_has_requested_name(self):
        from web_browse import describe
        self.assertEqual(describe({'role':'link','label':'Result'}),'Link Result.')
        self.assertIn(('Web browser','web browser'),app.MAIN_MENU_CHOICES)
    def test_navigation_readiness_runs_before_virtual_snapshot(self):
        calls=[];self.backend.wait_ready=lambda:calls.append('ready')
        original=self.backend.snapshot
        def snapshot():calls.append('snapshot');return original()
        self.backend.snapshot=snapshot;self.session._refresh()
        self.assertEqual(calls[:2],['ready','snapshot'])

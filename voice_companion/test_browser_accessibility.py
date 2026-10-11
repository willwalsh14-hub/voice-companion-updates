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
                self.assertIn('Page:',status.call_args.args[0]);self.assertIn(unittest.mock.call('web',True),sync.call_args_list)
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
                app.process_keyboard_batch('web');status.assert_called_once();self.assertIn('Page:',status.call_args.args[0])
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

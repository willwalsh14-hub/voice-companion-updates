import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import companion as app
from app_updates import AppUpdates
from update_voice import Microphone,ProgressSpeech,status_request,progress
from web_assistant import BrowserBackend,WebSession,WebError
from test_web_assistant import FakeBackend

class UpdateRecoveryTests(unittest.TestCase):
    def test_first_page_opens_from_keyboard_bookmark_menu(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend())
            session.favorites.add('News','https://fixture.test')
            with patch.object(app,'web_session',session),patch.object(app,'speak'),patch.object(app,'APP_WINDOW',Mock()),patch.object(app,'KEYBOARD_SPEECH',None):
                mode=app.open_browser_menu()
                for key in ('Down','Down','Enter','Enter'):
                    mode=app.handle(app.navigation_key(key,mode),mode)
                self.assertEqual(mode,'web')
                self.assertTrue(session.navigation_succeeded)
                self.assertGreater(session.page_revision,0)

    def test_native_speech_cannot_block_listener_shutdown(self):
        import sys,time,types
        entered=threading.Event();release=threading.Event();voice=Mock()
        def speak(*args):entered.set();release.wait(5)
        voice.Speak.side_effect=speak
        client=types.ModuleType('win32com.client');client.Dispatch=Mock(return_value=voice)
        parent=types.ModuleType('win32com');parent.__path__=[];parent.client=client
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'pythoncom':Mock(),'win32com':parent,'win32com.client':client}):
            speech=ProgressSpeech(folder)
            try:
                speech.say(37);self.assertTrue(entered.wait(1))
                started=time.monotonic();speech.close()
                self.assertLess(time.monotonic()-started,3)
                self.assertTrue(speech.stopped.is_set())
            finally:release.set();speech.thread.join(1)

    def test_status_interrupts_download_speech_and_preparing_remains_responsive(self):
        service=SimpleNamespace(percent=37,phase='downloading')
        with patch.object(app,'UPDATES',service),patch.object(app,'speak') as say,patch.object(app,'interrupt_speech') as stop:
            self.assertTrue(app.web_feedback_control('status','update_download'))
            self.assertTrue(app.web_feedback_control('no','update_offer'))
            self.assertFalse(app.web_feedback_control('thirty seven percent','update_download'))
            app.handle('status','update_download');stop.assert_called_once();say.assert_called_with('Downloading the update. 37 percent.')
            service.phase='preparing';app.handle('status','update_download');say.assert_called_with('Preparing installation.')
            app.handle('cancel update','update_download');say.assert_called_with('Preparing installation. Voice Companion will reopen with the microphone on.')

    def test_update_restart_is_awake_without_another_update_offer(self):
        service=Mock()
        with patch.object(app.sys,'argv',['companion','--resume-after-update']),patch.object(app,'UPDATES',service),patch.object(app,'speak'),patch.object(app,'announce_main_menu') as menu:
            self.assertEqual(app.startup_update_mode(),'awake');service.check.assert_not_called();menu.assert_called_once()

    def test_handoff_preparation_is_async_and_reports_success_or_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            service=AppUpdates(folder,'0.2.125-test');entered=threading.Event();release=threading.Event()
            def launch(*args):entered.set();release.wait(2)
            with patch.object(service,'launch',side_effect=launch):
                self.assertTrue(service.begin_install('file',{}));self.assertTrue(entered.wait(1))
                self.assertEqual(service.phase,'preparing');self.assertFalse(service.begin_install('other',{}))
                release.set();self.assertEqual(service.events.get(timeout=2),('install_ready',None))
            with patch.object(service,'launch',side_effect=OSError('device')):
                self.assertTrue(service.begin_install('file',{}));self.assertEqual(service.events.get(timeout=2),('install_failed',None))

    def test_main_microphone_retries_after_driver_busy_and_closes_failed_streams(self):
        sd=Mock();broken=Mock();broken.start.side_effect=OSError('busy');working=Mock()
        sd.RawInputStream.side_effect=[broken,working]
        with patch.object(app,'microphone_settings',return_value=({},48000,1)),patch.object(app.time,'sleep'):
            self.assertIs(app.open_microphone(sd),working)
        broken.close.assert_called_once();working.start.assert_called_once()

    def test_listener_closes_input_even_if_stop_raises(self):
        listener=Microphone.__new__(Microphone);listener.stream=Mock();stream=listener.stream;stream.stop.side_effect=OSError('closed')
        listener.close();stream.close.assert_called_once();self.assertIsNone(listener.stream);listener.close()
        self.assertTrue(status_request('Update status.'));self.assertFalse(status_request('Installing update thirty seven percent'))
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(progress(folder),0);Path(folder,'install-percent.txt').write_text('37');self.assertEqual(progress(folder),37)

class BookmarkRecoveryTests(unittest.TestCase):
    def test_closed_browser_cleanup_continues_after_context_failure(self):
        backend=BrowserBackend('folder');backend.context=Mock();backend.playwright=Mock();backend.page=Mock()
        backend.context.close.side_effect=RuntimeError('already closed');driver=backend.playwright
        backend.close();driver.stop.assert_called_once();self.assertIsNone(backend.page);backend.close()

    def test_menu_opens_saved_address_after_browser_closed_and_failure_stays_menu(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeBackend();backend.close=Mock();session=WebSession(folder,backend)
            session.open('example.com/News?part=One#Top');session.favorites.add('News',session.snapshot['url']);session.leave(False)
            with patch.object(app,'web_session',session),patch.object(app,'APP_WINDOW',None),patch.object(app,'speak'),patch.object(app,'PREFERENCES',{'web_keep_open':False}):
                app.handle('go to bookmark','browser_menu');self.assertEqual(app.handle('enter','browser_menu'),'web')
                self.assertEqual(session.snapshot['url'],'https://example.com/News?part=One#Top')
                backend.goto=Mock(side_effect=WebError('Browser not ready'))
                app.handle('go to bookmark','browser_menu');self.assertEqual(app.handle('enter','browser_menu'),'browser_menu')
                self.assertIsNotNone(session.favorites.get('News'))

    def test_missing_selected_browser_uses_included_firefox_without_changing_preference(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend());old=Mock(browser='edge');old.goto.side_effect=WebError('not installed')
            session.backend=old;session.injected_backend=False;session.browser='edge'
            fallback=FakeBackend();fallback.browser='firefox'
            with patch('web_assistant.BrowserBackend',return_value=fallback):
                self.assertIn('included Firefox',session.open('example.com'))
            self.assertTrue(session.navigation_succeeded);self.assertEqual(session.browser,'edge');old.close.assert_called_once()

if __name__=='__main__':unittest.main()

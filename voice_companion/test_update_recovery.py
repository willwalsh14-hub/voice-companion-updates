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

    def test_update_window_stays_ready_when_input_driver_is_temporarily_unavailable(self):
        import json,update_voice
        microphone=Mock();microphone.stream=None;microphone.open.side_effect=RuntimeError('Input driver busy')
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'voice-stop').write_text('stop')
            with patch('vosk.Model'),patch.object(update_voice,'Microphone',return_value=microphone),patch.object(update_voice,'ProgressSpeech'):
                self.assertEqual(update_voice.run(folder,headless=True),0)
            self.assertTrue(json.loads((Path(folder)/'voice-ready.json').read_text())['ready'])
            microphone.open.assert_called_once();microphone.close.assert_called_once()

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
            app.handle('status','update_download');stop.assert_called_once();say.assert_called_with('37 percent.')
            service.phase='preparing';app.handle('status','update_download');say.assert_called_with('37 percent.')
            app.handle('cancel update','update_download');say.assert_called_with('Preparing installation. Voice Companion will reopen in sleep mode.')

    def test_update_restart_stays_asleep_without_another_update_offer(self):
        service=Mock()
        with patch.object(app.sys,'argv',['companion','--resume-after-update']),patch.object(app,'UPDATES',service),patch.object(app,'speak'),patch.object(app,'announce_main_menu') as menu:
            self.assertEqual(app.startup_update_mode(),'sleep');service.check.assert_not_called();menu.assert_not_called()

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

class PlaybackAndExitTests(unittest.TestCase):
    def test_shared_shortcut_preserves_every_app_mode(self):
        hub=Mock();hub.command.return_value='Audio paused.'
        for mode in ('awake','sleep','media','podcast','document','email_draft','web','browser_menu','settings','help','tutorial','update_download'):
            with self.subTest(mode=mode),patch.object(app,'MEDIA_HUB',hub),patch.object(app,'APP_WINDOW',None),patch.object(app,'interrupt_speech'),patch.object(app,'speak'):
                command=app.navigation_key('Ctrl+Alt+P',mode)
                self.assertEqual(command,'toggle audio')
                self.assertEqual(app.handle(command,mode),mode)
                hub.command.assert_called_with('toggle audio')

    def test_main_menu_stops_audio_unless_background_option_is_on(self):
        from settings_model import DEFAULTS,fields
        self.assertFalse(DEFAULTS['keep_audio_playing'])
        for category in ('Radio','Podcasts'):
            self.assertIn('keep_audio_playing',[f.key for f in fields(category,{})])
        for keep in (False,True):
            hub=Mock()
            with patch.object(app,'MEDIA_HUB',hub),patch.object(app,'PREFERENCES',{'keep_audio_playing':keep}),patch.object(app,'APP_WINDOW',None),patch.object(app,'speak'),patch.object(app,'announce_main_menu'):
                self.assertEqual(app.handle('main menu','media'),'awake')
                if keep:hub.audio_command.assert_not_called()
                else:hub.audio_command.assert_called_once_with('stop audio')

    def test_shutdown_is_spoken_outside_keyboard_batch_and_waits(self):
        seen=[]
        with patch.object(app,'MEDIA_HUB',None),patch.object(app,'web_session',None),patch.object(app,'email_draft',None),patch.object(app,'flush_note'),patch.object(app,'interrupt_speech'),patch.object(app,'KEYBOARD_BATCH',True),patch.object(app,'speak',side_effect=lambda text:seen.append((text,app.KEYBOARD_BATCH))),patch.object(app,'wait_for_speech',return_value=True) as wait:
            self.assertEqual(app.finish_exit('awake'),'exit')
            self.assertEqual(seen,[('Shutting down. Goodbye.',False)])
            wait.assert_called_once_with(30000)

    @unittest.skipUnless(__import__('sys').platform=='win32','Actual Windows speech')
    def test_exit_waits_for_complete_sapi_audio(self):
        import win32com.client,wave
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'goodbye.wav'
            voice=win32com.client.Dispatch('SAPI.SpVoice');stream=win32com.client.Dispatch('SAPI.SpFileStream')
            stream.Open(str(path),3);voice.AudioOutputStream=stream
            with patch.object(app,'voice',voice,create=True),patch.object(app,'TEXT_MODE',False),patch.object(app,'KEYBOARD_SPEECH',None),patch.object(app,'ESPEAK_SPEECH',None),patch.object(app,'AI_SPEECH',None),patch.object(app,'APP_WINDOW',None),patch.object(app,'MEDIA_HUB',None),patch.object(app,'web_session',None),patch.object(app,'email_draft',None),patch.object(app,'flush_note'),patch.object(app,'KEYBOARD_BATCH',True):
                self.assertEqual(app.finish_exit('awake'),'exit')
                self.assertTrue(voice.WaitUntilDone(0),'Exit returned before speech finished')
            stream.Close()
            with wave.open(str(path),'rb') as audio:
                self.assertGreater(audio.getnframes()/audio.getframerate(),1,'Goodbye audio was cut short')

    @unittest.skipUnless(__import__('sys').platform=='win32','Windows global hotkey')
    def test_registered_hotkey_message_toggles_once_then_releases(self):
        import ctypes
        from media_shortcut import MediaShortcut
        seen=[];received=threading.Event()
        def toggled():seen.append('toggle');received.set()
        shortcut=MediaShortcut(toggled)
        try:
            self.assertTrue(shortcut.register(),'Control+Alt+P could not register')
            self.assertTrue(ctypes.windll.user32.PostThreadMessageW(shortcut.thread_id,shortcut.MESSAGE,shortcut.HOTKEY_ID,0))
            self.assertTrue(received.wait(2),'Hotkey message was not received')
            self.assertEqual(seen,['toggle'])
        finally:shortcut.close()
        self.assertFalse(shortcut.registered)

class ExitRecoveryTests(unittest.TestCase):
    def test_disconnected_microphone_cleanup_can_run_twice(self):
        stream=Mock();stream.close.side_effect=RuntimeError('device disconnected')
        type(stream).active=__import__('unittest.mock',fromlist=['PropertyMock']).PropertyMock(side_effect=RuntimeError('device gone'))
        self.assertFalse(app.microphone_active(stream))
        app.close_microphone(stream);app.close_microphone(stream)
        self.assertEqual(stream.close.call_count,2)

    def test_audio_cleanup_continues_after_both_resources_fail(self):
        from media_player import MediaPlayer
        player=MediaPlayer('folder');player.context=Mock();player.playwright=Mock()
        context,driver=player.context,player.playwright
        context.close.side_effect=RuntimeError('closed');driver.stop.side_effect=RuntimeError('gone')
        player.close();player.close()
        context.close.assert_called_once();driver.stop.assert_called_once()
        self.assertIsNone(player.context);self.assertIsNone(player.playwright)

    def test_exit_speaks_goodbye_after_cleanup_errors(self):
        hub=Mock();hub.player.close.side_effect=RuntimeError('gone')
        with patch.object(app,'MEDIA_HUB',hub),patch.object(app,'web_session',None),patch.object(app,'email_draft',None),patch.object(app,'flush_note'),patch.object(app,'interrupt_speech',side_effect=RuntimeError('output changed')),patch.object(app,'speak_status') as say,patch.object(app,'wait_for_speech') as wait:
            self.assertEqual(app.finish_exit('awake'),'exit')
            say.assert_called_once_with('Shutting down. Goodbye.');wait.assert_called_once_with(30000)

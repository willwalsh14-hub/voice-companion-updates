import contextlib,io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from urllib import request,error
from network_security import NoServiceRedirect,secure_service_open
from privacy_controls import narration_allowed,cloud_recognition_allowed

class SecurityPrivacyTests(unittest.TestCase):
    def test_credentials_are_not_forwarded_on_any_redirect(self):
        outgoing=request.Request('https://api.example.com/private',headers={'Authorization':'Bearer PRIVATE'},data=b'PRIVATE TEXT')
        handler=NoServiceRedirect()
        for address in ('http://api.example.com/private','https://attacker.test/','https://api.example.com/other'):
            with self.subTest(address=address),self.assertRaises(error.URLError):handler.redirect_request(outgoing,None,302,'Found',{},address)

    def test_unsafe_service_addresses_are_rejected_before_a_connection(self):
        with patch('network_security.request.build_opener') as build:
            for address in ('http://service.test/','file:///private','https://user:secret@service.test/','https://service.test:8443/'):
                with self.subTest(address=address),self.assertRaises(error.URLError):secure_service_open(address)
            build.assert_not_called()

    def test_saved_key_or_environment_key_does_not_grant_permission(self):
        import ai_voice_settings
        self.assertFalse(narration_allowed({}, {'key':'KEY'}))
        self.assertFalse(narration_allowed({'ai_consent':False},{'key':'KEY','consent':True}))
        self.assertTrue(narration_allowed({}, {'key':'KEY','consent':True}))
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'VOICE_COMPANION_OPENAI_KEY':'KEY'}):
            self.assertFalse(narration_allowed({},ai_voice_settings.load(folder)))

    def test_ai_engine_is_not_created_without_permission(self):
        import companion as app
        with patch('ai_voice_settings.load',return_value={'key':'KEY'}),patch.object(app,'PREFERENCES',{}),patch.object(app,'AI_SPEECH',None),patch('ai_speech.AISpeech') as engine:
            self.assertFalse(app.configure_ai_speech());engine.assert_not_called()

    def test_online_recognition_is_off_by_default_and_stays_off_in_private_workflows(self):
        from settings_model import DEFAULTS,fields
        self.assertFalse(DEFAULTS['cloud_consent'])
        self.assertIn('cloud_consent',[f.key for f in fields('Input',{})])
        for mode in ('awake','document','email_draft','web','settings','sleep'):
            self.assertFalse(cloud_recognition_allowed(mode,{}))
        for mode in ('web','settings','sleep','exit'):
            self.assertFalse(cloud_recognition_allowed(mode,{'cloud_consent':True}))
        self.assertFalse(cloud_recognition_allowed('awake',{'cloud_consent':True},account_setup=True))
        self.assertTrue(cloud_recognition_allowed('document',{'cloud_consent':True}))

    def test_normal_narration_does_not_write_user_content_to_console(self):
        import companion as app
        out=io.StringIO()
        with contextlib.redirect_stdout(out),patch.object(app,'TEXT_MODE',False),patch.object(app,'APP_WINDOW',None),patch.object(app,'KEYBOARD_NAVIGATION',False),patch.object(app,'ESPEAK_SPEECH',None),patch.object(app,'AI_SPEECH',None),patch.object(app,'voice',Mock(),create=True),patch.object(app,'update_audio_ducking'),patch.object(app,'sapi_speak'):
            app.speak('PRIVATE DOCUMENT TEXT')
        self.assertNotIn('PRIVATE DOCUMENT TEXT',out.getvalue())

    def test_mail_paging_cannot_send_token_to_a_different_port_or_embedded_user(self):
        from mailbox_access import MailboxClient,MailboxError
        opener=Mock();client=MailboxClient('outlook','TOKEN',opener)
        for url in ('https://graph.microsoft.com:8443/v1.0/me/messages','https://user:pass@graph.microsoft.com/v1.0/me/messages'):
            with self.subTest(url=url),self.assertRaises(MailboxError):client._call(url)
        opener.assert_not_called()

if __name__=='__main__':unittest.main()

class ThemeAndCleanupTests(unittest.TestCase):
    def test_blue_link_is_replaced_on_black_and_remains_readable_on_white(self):
        from visual_accessibility import contrast,readable_color
        colors={'black':(0,0,0),'white':(65535,65535,65535),'SystemHotLight':(0,0,65535),'#000000':(0,0,0),'#ffffff':(65535,65535,65535)}
        widget=Mock();widget.winfo_rgb.side_effect=colors.__getitem__
        for background,foreground in (('black','white'),('white','black')):
            widget.cget.side_effect=lambda key:background if key=='background' else foreground
            color=readable_color(widget)
            self.assertGreaterEqual(contrast(colors[color],colors[background]),4.5)
            if background=='black':self.assertEqual(color,'white')

    def test_private_workflow_cleanup_closes_stream_when_cloud_stop_fails(self):
        from cloud_recognition import CloudRecognition
        cloud=CloudRecognition.__new__(CloudRecognition);cloud.active=True
        stream=cloud.stream=Mock();cloud.recognizer=Mock()
        cloud.recognizer.stop_continuous_recognition_async.side_effect=RuntimeError('offline')
        cloud.stop();cloud.stop()
        stream.close.assert_called_once();self.assertFalse(cloud.active)
        self.assertIsNone(cloud.recognizer);self.assertIsNone(cloud.stream)

class SourceSecurityChecks(unittest.TestCase):
    def test_scanner_blocks_unsafe_patterns_without_printing_the_secret(self):
        from check_security import scan
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);secret='sk-'+'X'*32
            (root/'unsafe.py').write_text('key='+repr(secret)+'\nsubprocess.run(command,shell=True)\neval(user_input)\n')
            results=scan(root)
            self.assertEqual(len(results),3)
            self.assertNotIn(secret,json.dumps(results))

class SavedAddressPrivacyTests(unittest.TestCase):
    def test_login_secrets_are_not_written_to_history_or_new_favorites(self):
        from browser_menu import BrowserMenu
        from web_assistant import Favorites,WebError
        with tempfile.TemporaryDirectory() as folder:
            menu=BrowserMenu(folder);favorites=Favorites(folder)
            address='https://example.test/callback?code=PRIVATE&state=SECRET'
            menu.record({'url':address,'title':'Sign in'})
            self.assertFalse(menu.path.exists())
            with self.assertRaises(WebError):favorites.add('Sign in',address)
            self.assertFalse(favorites.path.exists())

    def test_old_saved_secrets_are_removed_and_ordinary_addresses_stay_exact(self):
        from browser_menu import BrowserMenu
        from web_assistant import Favorites
        from privacy_controls import saved_address
        ordinary='https://example.test/News?part=One#Top'
        self.assertEqual(saved_address(ordinary),ordinary)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'web-history.json').write_text(json.dumps([{'name':'Sign in','url':'https://example.test/?access_token=PRIVATE'},{'name':'News','url':ordinary}]))
            menu=BrowserMenu(folder);self.assertEqual(menu.history(),[{'name':'News','url':ordinary}])
            self.assertNotIn('PRIVATE',menu.path.read_text())
            favorites=Favorites(folder);favorites.path.write_text(json.dumps({'news':{'name':'News','url':ordinary},'login':{'name':'Login','url':'https://example.test/welcome#id_token=PRIVATE'}}))
            self.assertEqual(favorites.get('Login')['url'],'https://example.test/welcome')
            self.assertEqual(favorites.get('News')['url'],ordinary)
            self.assertNotIn('PRIVATE',favorites.path.read_text())

class VisibleStatusTests(unittest.TestCase):
    def test_spoken_keyboard_notice_is_also_shown_as_text(self):
        from app_window import CompanionWindow
        import threading,queue
        window=CompanionWindow.__new__(CompanionWindow);window.closed=threading.Event();window.messages=queue.Queue()
        window.keyboard_output=Mock(problem=None)
        window.feedback('Settings were not saved.')
        self.assertEqual(window.messages.get_nowait(),'Settings were not saved.')
        window.keyboard_output.speak.assert_called_once_with('Settings were not saved.')

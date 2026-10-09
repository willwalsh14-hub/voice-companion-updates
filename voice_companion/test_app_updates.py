import hashlib,json,queue,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app_updates import AppUpdates,download_release,fetch_release,validate_release,version_key
from update_publisher import release_manifest
import companion

class Response:
    def __init__(self,body,url='https://example.org/file'): self.body=body;self.url=url
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def geturl(self): return self.url
    def read(self,size): result=self.body[:size];self.body=self.body[size:];return result

class UpdateTests(unittest.TestCase):
    def release(self,body=b'MZinstaller'):
        return {'schema':1,'version':'1.2.3-test','installer_url':'https://example.org/Setup.exe','sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}
    def test_versions_and_manifest_validation(self):
        for old in ('1.2.1-test','1.2.2-test','1.2.2'):
            self.assertIsNotNone(validate_release(self.release(),old))
        for current in ('1.2.3-test','1.2.3','1.2.4-test'):
            self.assertIsNone(validate_release(self.release(),current))
        for values in ({'installer_url':'http://example.org/a'},{'sha256':'wrong'},{'size':0},{'size':True},{'version':'bad'}):
            with self.assertRaises((ValueError,TypeError)): validate_release(self.release()|values,'1.2.2-test')
    def test_download_verified_canceled_corrupted_truncated_html(self):
        with tempfile.TemporaryDirectory() as folder:
            event=threading.Event();body=b'MZinstaller'
            with patch('app_updates.urllib.request.urlopen',return_value=Response(body)):
                self.assertEqual(download_release(self.release(),folder,event).read_bytes(),body)
            for body in (b'MZwrong',b'MZ',b'MZinstallerextra'):
                with patch('app_updates.urllib.request.urlopen',return_value=Response(body)),self.assertRaises(ValueError): download_release(self.release(),folder,event)
            body=b'<html>error</html>'
            with patch('app_updates.urllib.request.urlopen',return_value=Response(body)),self.assertRaises(ValueError):download_release(self.release(body),folder,event)
            event.set()
            with patch('app_updates.urllib.request.urlopen',return_value=Response(b'MZinstaller')),self.assertRaises(InterruptedError):download_release(self.release(),folder,event)
            self.assertFalse((Path(folder)/'VoiceCompanion-Setup.part').exists())
    def test_fetch_rejects_oversize_and_insecure_redirect(self):
        for response in (Response(b'x'*65537),Response(b'{}','http://example.org/x')):
            with patch('app_updates.urllib.request.urlopen',return_value=response),self.assertRaises(ValueError):fetch_release('https://example.org/feed','1.2.2-test')
    def test_configuration_and_background_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);service=AppUpdates(root,'1.2.2-test',root)
            self.assertFalse(service.check(True));self.assertEqual(service.events.get()[0],'unconfigured')
            (root/'update-settings.json').write_text(json.dumps({'manifest_url':'https://example.org/feed'}))
            service=AppUpdates(root,'1.2.2-test',root)
            with patch('app_updates.fetch_release',side_effect=OSError('offline')):
                service.check(True);self.assertEqual(service.events.get(timeout=2),('check_failed',True))
    def test_source_mode_cannot_launch_an_installer(self):
        with tempfile.TemporaryDirectory() as folder,patch('app_updates.subprocess.Popen') as launch:
            service=AppUpdates(folder,'1.2.2-test');self.assertFalse(service.can_install())
            with self.assertRaises(ValueError):service.launch(Path(folder)/'Setup.exe',self.release())
            launch.assert_not_called()
    def test_spoken_offer_no_yes_cancel_and_safe_handoff(self):
        service=Mock();service.events=queue.Queue();service.busy=False;service.canceled=threading.Event();service.download.return_value=True
        with patch.object(companion,'UPDATES',service),patch.object(companion,'UPDATE_OFFER',False),patch.object(companion,'UPDATE_MANUAL',False),patch.object(companion,'UPDATE_RETURN_MODE','awake'),patch.object(companion,'speak') as speak,patch.object(companion,'speech_busy',return_value=False),patch.object(companion,'document',None),patch.object(companion,'email_draft',None),patch.object(companion,'finish_mail_announcement'):
            service.events.put(('available',self.release()));self.assertEqual(companion.poll_app_updates('sleep'),'update_offer')
            speak.assert_called_with('A new update is available. Would you like to install it now? Say yes or no.')
            self.assertEqual(companion.handle('no','update_offer'),'awake');service.download.assert_not_called()
            self.assertEqual(companion.handle('yes','update_offer'),'update_download');service.download.assert_called_once()
            companion.handle('cancel update','update_download');self.assertTrue(service.canceled.is_set())
            service.events.put(('downloaded',(Path('fake.exe'),self.release())));self.assertEqual(companion.poll_app_updates('update_download'),'update_download');service.launch.assert_not_called()
            service.canceled.clear();service.events.put(('downloaded',(Path('fake.exe'),self.release())))
            self.assertEqual(companion.poll_app_updates('update_download'),'exit');service.launch.assert_called_once()
    def test_automatic_offer_waits_until_task_is_finished(self):
        service=Mock();service.events=queue.Queue()
        with patch.object(companion,'UPDATES',service),patch.object(companion,'UPDATE_OFFER',False),patch.object(companion,'UPDATE_MANUAL',False),patch.object(companion,'speech_busy',return_value=False),patch.object(companion,'speak'):
            service.events.put(('available',self.release()));self.assertEqual(companion.poll_app_updates('document'),'document')
            self.assertEqual(companion.poll_app_updates('awake'),'update_offer')
    def test_publisher_manifest_and_packaging(self):
        with tempfile.TemporaryDirectory() as folder:
            setup=Path(folder)/'Setup.exe';setup.write_bytes(b'MZinstaller')
            self.assertEqual(release_manifest(setup,'1.2.3-test','https://example.org/Setup.exe'),self.release())
        root=Path(__file__).parent;builder=(root/'build-windows.ps1').read_text()
        self.assertIn("'test_app_updates'",builder)
        for name in ('update-settings.json','apply-update.ps1'):self.assertIn(name+':.',builder)

class UpdateHandoffTests(unittest.TestCase):
    release = UpdateTests.release
    def test_windows_handoff_uses_verified_installer_and_detached_helper(self):
        import types,os
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);app=root/'Programs'/'Voice Companion'/'VoiceCompanion.exe';app.parent.mkdir(parents=True);app.write_bytes(b'MZapp')
            download=root/'download';download.mkdir();setup=download/'Setup.exe';setup.write_bytes(b'MZinstaller')
            service=AppUpdates(root,'1.2.2-test')
            fake_os=types.SimpleNamespace(name='nt',environ={'LOCALAPPDATA':str(root)},getpid=lambda:123)
            fake_sys=types.SimpleNamespace(frozen=True,executable=str(app))
            with patch('app_updates.os',fake_os),patch('app_updates.sys',fake_sys),patch('app_updates.subprocess.Popen') as launch:
                service.launch(setup,self.release())
                args=launch.call_args.args[0]
                self.assertIn('apply-update.ps1',Path(args[args.index('-File')+1]).name)
                self.assertEqual(args[args.index('-PreviousProcessId')+1],'123')
                setup.write_bytes(b'MZchanged')
                with self.assertRaises(ValueError):service.launch(setup,self.release())
                self.assertEqual(launch.call_count,1)
    def test_save_failure_does_not_download_or_close_app(self):
        service=Mock();document=Mock();document.save.side_effect=OSError('cannot save')
        with patch.object(companion,'UPDATES',service),patch.object(companion,'UPDATE_RETURN_MODE','document'),patch.object(companion,'document',document),patch.object(companion,'email_draft',None),patch.object(companion,'speak'):
            self.assertEqual(companion.handle('yes','update_offer'),'document')
            service.download.assert_not_called();service.launch.assert_not_called()
    def test_failed_download_and_handoff_restore_original_task(self):
        service=Mock();service.events=queue.Queue();service.canceled=threading.Event();service.launch.side_effect=OSError('cannot start')
        with patch.object(companion,'UPDATES',service),patch.object(companion,'UPDATE_RETURN_MODE','email_draft'),patch.object(companion,'UPDATE_OFFER',False),patch.object(companion,'speak'),patch.object(companion,'finish_mail_announcement'):
            service.events.put(('download_failed',None));self.assertEqual(companion.poll_app_updates('update_download'),'email_draft')
            service.events.put(('downloaded',(Path('Setup.exe'),self.release())));self.assertEqual(companion.poll_app_updates('update_download'),'email_draft')
    def test_installer_helper_does_not_force_restart_or_require_admin(self):
        root=Path(__file__).parent;helper=(root/'apply-update.ps1').read_text();installer=(root/'VoiceCompanion.iss').read_text()
        for value in ('WaitForExit(30000)','Get-FileHash','/VERYSILENT','/NORESTART','/RESTARTEXITCODE=3010','/NORESTARTAPPLICATIONS','update-result.json'):self.assertIn(value,helper)
        self.assertIn('PrivilegesRequired=lowest',installer)
        self.assertNotIn('-Verb RunAs',helper)


class ConfiguredReleaseFeedTests(unittest.TestCase):
    def test_check_uses_wills_bundled_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            service=AppUpdates(folder,'1.2.2-test')
            expected='https://github.com/willwalsh14-hub/voice-companion-updates/releases/latest/download/voice-companion-update.json'
            self.assertEqual(service.manifest_url,expected)
            with patch('app_updates.fetch_release',return_value=None) as fetch:
                self.assertTrue(service.check())
                self.assertEqual(service.events.get(timeout=2),('current',False))
                fetch.assert_called_once_with(expected,'1.2.2-test')

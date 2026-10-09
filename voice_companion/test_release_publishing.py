import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from publish_release import publish, gh_call

class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.setup=self.root/'VoiceCompanion-Setup.exe';self.setup.write_bytes(b'MZtested installer')
        (self.root/'companion.py').write_text("APP_VERSION = '1.2.3-test'\n")
        (self.root/'update-settings.json').write_text(json.dumps({'manifest_url':'https://github.com/owner/repository/releases/latest/download/voice-companion-update.json'}))
        (self.root/'VoiceCompanion-Build-Passed.json').write_text(json.dumps({'version':'1.2.3-test','sha256':hashlib.sha256(self.setup.read_bytes()).hexdigest(),'size':self.setup.stat().st_size}),encoding='utf-8-sig')
        (self.root/'espeak').mkdir()
        for name in ('espeak-ng-1.52.0-source.tar.gz','COPYING'): (self.root/'espeak'/name).write_bytes(b'source or license')
        self.commands=[];self.existing=None;self.latest=None;self.corrupt=False;self.fail_upload=False
        self.metadata={'private':False,'permissions':{'push':True}}
    def gh(self, executable, *args, **kwargs):
        self.commands.append(args)
        if args[0]=='api':
            if args[1]=='repos/owner/repository':return json.dumps(self.metadata)
            if 'tags/' in args[1]:return json.dumps(self.existing) if self.existing else None
            if any(cmd[:2]==('release','edit') for cmd in self.commands):return json.dumps({'tag_name':'v1.2.3-test','draft':False,'prerelease':False,'html_url':'https://github.com/owner/repository/releases/tag/v1.2.3-test'})
            return json.dumps(self.latest) if self.latest else None
        if args[1]=='upload' and self.fail_upload:raise RuntimeError('offline')
        if args[1]=='download':
            name=args[args.index('--pattern')+1];target=Path(args[args.index('--dir')+1])/name
            local=self.root/'release-upload'/name
            if not local.exists():local=self.root/'espeak'/name
            shutil.copy2(local,target)
            if self.corrupt:target.write_bytes(b'corrupted')
        return ''
    def run_publish(self):
        with patch('publish_release.gh_call',side_effect=self.gh):return publish(self.setup,root=self.root)
    def test_complete_release_is_verified_before_latest(self):
        self.assertTrue(self.run_publish().endswith('v1.2.3-test'))
        edit=next(i for i,c in enumerate(self.commands) if c[:2]==('release','edit'))
        downloads=[i for i,c in enumerate(self.commands) if c[:2]==('release','download')]
        self.assertEqual(len(downloads),4);self.assertTrue(all(i<edit for i in downloads))
        self.assertIn('--draft',next(c for c in self.commands if c[:2]==('release','create')))
        self.assertIn('--latest',self.commands[edit])
        manifest=json.loads((self.root/'release-upload'/'voice-companion-update.json').read_text())
        self.assertEqual(manifest['sha256'],hashlib.sha256(self.setup.read_bytes()).hexdigest())
    def test_workflow_token_without_permission_metadata_uses_authorized_writes(self):
        self.metadata={'private':False}
        self.assertTrue(self.run_publish().endswith('v1.2.3-test'))
        self.assertTrue(any(c[:2]==('release','create') for c in self.commands))
    def test_private_or_explicit_read_only_repository_is_rejected(self):
        for metadata in ({'private':True},{'private':False,'permissions':{'push':False}},{}):
            self.metadata=metadata;self.commands=[]
            with self.assertRaises(ValueError):self.run_publish()
            self.assertFalse(any(c[0]=='release' for c in self.commands))
    def test_failed_or_corrupt_upload_never_publishes(self):
        for failure in ('corrupt','fail_upload'):
            setattr(self,failure,True);self.commands=[]
            with self.assertRaises((ValueError,RuntimeError)):self.run_publish()
            self.assertFalse(any(c[:2]==('release','edit') for c in self.commands))
            setattr(self,failure,False)
    def test_draft_retry_does_not_create_another_release(self):
        self.existing={'draft':True};self.run_publish()
        self.assertFalse(any(c[:2]==('release','create') for c in self.commands))
    def test_published_version_or_downgrade_is_never_overwritten(self):
        for existing,latest in (({'draft':False},None),(None,{'tag_name':'v1.2.4-test'})):
            self.existing=existing;self.latest=latest;self.commands=[]
            with self.assertRaises(ValueError):self.run_publish()
            self.assertFalse(any(c[0]=='release' for c in self.commands))
    def test_changed_or_unchecked_installer_is_not_uploaded(self):
        self.setup.write_bytes(b'MZchanged')
        with self.assertRaises(ValueError):self.run_publish()
        self.assertEqual(self.commands,[])
        (self.root/'VoiceCompanion-Build-Passed.json').unlink()
        with self.assertRaises(OSError):self.run_publish()
    def test_builder_publishes_after_installed_checks_and_not_in_auth_transcript(self):
        script=(Path(__file__).parent/'build-windows.ps1').read_text()
        self.assertGreater(script.rindex("  Invoke-BuildPublishing"),script.index('INSTALL PASSED.'))
        self.assertLess(script.index('Stop-Transcript'),script.rindex("  Invoke-BuildPublishing"))
        self.assertIn('if (-not $NoPublish)',script)
    def test_only_not_found_is_treated_as_missing(self):
        from unittest.mock import MagicMock
        for stderr,missing in (('HTTP 404',True),('HTTP 403',False)):
            process=MagicMock();process.__enter__.return_value=process
            process.returncode=1;process.communicate.return_value=('',stderr)
            with patch('publish_release.subprocess.Popen',return_value=process):
                if missing:self.assertIsNone(gh_call('gh','api','path',missing_ok=True))
                else:
                    with self.assertRaises(RuntimeError):gh_call('gh','api','path',missing_ok=True)

    def test_progress_heartbeat_and_child_completion(self):
        import subprocess
        from unittest.mock import MagicMock
        process=MagicMock();process.__enter__.return_value=process;process.returncode=0
        process.communicate.side_effect=[subprocess.TimeoutExpired('gh',20),('done','')]
        with patch('publish_release.subprocess.Popen',return_value=process),patch('publish_release.progress') as output:
            self.assertEqual(gh_call('gh','release','upload','v1.2.3','C:/Folder with spaces/VoiceCompanion-Setup.exe'),'done')
        messages=[c.args[0] for c in output.call_args_list]
        self.assertTrue(any('Still waiting' in m for m in messages))
        self.assertTrue(messages[-1].endswith('completed.'))
        self.assertEqual(process.communicate.call_count,2)

    def test_builder_restarts_logging_and_requires_matching_publication(self):
        script=(Path(__file__).parent/'build-windows.ps1').read_text()
        self.assertIn('finally {',script)
        self.assertIn("'VoiceCompanion-Published.json'",script)
        self.assertIn('$published.sha256 -ne $built.sha256',script)
        self.assertGreater(script.index('BUILD, INSTALL, AND PUBLISH PASSED:'),script.index('$published.sha256 -ne $built.sha256'))

class ProgressProcessTests(unittest.TestCase):
    @unittest.skipIf(__import__('os').name == 'nt', 'POSIX child executable fixture')
    def test_real_waiting_process_shows_progress_and_keeps_raw_output_private(self):
        import sys
        import io
        import contextlib
        with tempfile.TemporaryDirectory(prefix='Publishing path ') as folder:
            cli=Path(folder)/'fake gh'
            cli.write_text('#!'+sys.executable+'\nimport time,sys\ntime.sleep(0.06)\nprint("metadata")\nprint("private diagnostic",file=sys.stderr)\n')
            cli.chmod(0o700)
            output=io.StringIO()
            with patch('publish_release.PROGRESS_INTERVAL',0.02),contextlib.redirect_stdout(output):
                result=gh_call(cli,'api','repos/owner/repository')
            self.assertEqual(result.strip(),'metadata')
            self.assertIn('Still waiting',output.getvalue())
            self.assertIn('completed.',output.getvalue())
            self.assertNotIn('private diagnostic',output.getvalue())

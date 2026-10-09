import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from github_login import ensure_login

class GitHubLoginTests(unittest.TestCase):
    def test_signed_out_status_opens_login_then_continues(self):
        run=Mock(side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(returncode=0),SimpleNamespace(returncode=0)])
        self.assertEqual(ensure_login('C:/Program Files/GitHub CLI/gh.exe',run),0)
        self.assertEqual(run.call_count,3)
        status,login,verified=run.call_args_list
        self.assertEqual(status.kwargs['stderr'],subprocess.DEVNULL)
        self.assertIn('login',login.args[0]);self.assertIn('--web',login.args[0])
        self.assertEqual(login.kwargs['stderr'],subprocess.STDOUT)
        self.assertEqual(verified.kwargs['stderr'],subprocess.DEVNULL)
    def test_existing_session_does_not_ask_for_login(self):
        run=Mock(return_value=SimpleNamespace(returncode=0))
        self.assertEqual(ensure_login('gh',run),0);self.assertEqual(run.call_count,1)
    def test_canceled_or_unverified_login_stops_build(self):
        for codes in ([1,1],[1,0,1]):
            run=Mock(side_effect=[SimpleNamespace(returncode=n) for n in codes])
            self.assertEqual(ensure_login('gh',run),1)
    def test_one_builder_prepares_auth_and_publishes_after_checks(self):
        root=Path(__file__).parent;script=(root/'build-windows.ps1').read_text()
        preflight=script.index('-PrepareOnly')
        self.assertLess(preflight,script.index('-m pip install'))
        self.assertLess(script.index('Stop-Transcript'),preflight)
        self.assertGreater(script.rindex("  Invoke-BuildPublishing"),script.index('INSTALL PASSED.'))
        helper=(root/'publish-windows.ps1').read_text()
        self.assertIn("'github_login.py'",helper)
        self.assertNotIn('& $gh auth status',helper)
        self.assertNotIn('& $gh auth login',helper)
        self.assertIn('if ($PrepareOnly)',helper)

class LoginProcessTests(unittest.TestCase):
    @unittest.skipIf(__import__('os').name == 'nt', 'POSIX executable fixture; Windows behavior is covered by the stream-routing assertions')
    def test_actual_signed_out_process_stderr_becomes_browser_login_guidance(self):
        import os,sys,tempfile,json
        with tempfile.TemporaryDirectory(prefix='GitHub login path ') as folder:
            root=Path(folder);fake=root/'fake gh';state=root/'session';calls=root/'calls'
            fake.write_text('#!'+sys.executable+'\n'+
                'import pathlib,sys\n'+
                'root=pathlib.Path(__file__).parent\n'+
                "with (root/'calls').open('a') as f:f.write(' '.join(sys.argv[1:])+'\\n')\n"+
                "if sys.argv[1:3]==['auth','status']:\n"+
                " if not (root/'session').exists():\n"+
                "  print('You are not logged into any GitHub hosts.',file=sys.stderr);sys.exit(1)\n"+
                "else:\n"+
                " print('ONE-TIME CODE: TEST-CODE. Press Enter to open your browser.',file=sys.stderr)\n"+
                " (root/'session').write_text('signed in')\n")
            fake.chmod(0o700)
            helper=Path(__file__).with_name('github_login.py')
            result=subprocess.run([sys.executable,str(helper),'--gh',str(fake)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(result.stderr,'')
            self.assertIn('TEST-CODE',result.stdout)
            self.assertIn('Continuing automatically',result.stdout)
            self.assertNotIn('You are not logged',result.stdout)
            self.assertEqual(len(calls.read_text().splitlines()),3)
            again=subprocess.run([sys.executable,str(helper),'--gh',str(fake)],capture_output=True,text=True)
            self.assertEqual(again.returncode,0);self.assertNotIn('TEST-CODE',again.stdout)

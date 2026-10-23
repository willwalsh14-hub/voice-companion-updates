import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from build_packaging import package

class PackagingTests(unittest.TestCase):
    def test_spaces_and_unicode_are_one_argument_and_logs_are_separate(self):
        with tempfile.TemporaryDirectory(prefix='User name ') as folder:
            root=Path(folder);options=root/'options.json';out=root/'output.txt';err=root/'errors.txt'
            args=['--add-data','START HERE - Veteran.txt:.','--add-data','model:model',
                  '--add-data','C:\\Users\\Amena Thomas\\Données:model']
            options.write_text(json.dumps(args),encoding='utf-8-sig')
            def run(argv,**kw):
                self.assertEqual(argv[3:-1],args)
                self.assertEqual(argv[-1],'companion.py')
                self.assertFalse(kw['shell'])
                kw['stdout'].write('output');kw['stderr'].write('error')
                return SimpleNamespace(returncode=2)
            self.assertEqual(package(options,out,err,runner=run),2)
            self.assertEqual((out.read_text(),err.read_text()),('output','error'))

    def test_build_failures_are_readable_without_the_full_log(self):
        # GitHub annotations can be read through the API when the log cannot.
        s=Path(__file__).with_name('build-windows.ps1').read_text()
        self.assertIn("Write-BuildAnnotation 'Build stopped' $_.Exception.Message",s)
        self.assertIn("Write-BuildAnnotation 'Release tests failed'",s)
        self.assertIn("'^(FAIL|ERROR): '",s)
        # A long note keeps its start, where the failed test names are.
        self.assertIn("$clean.Substring(0, 1200)",s)
        self.assertIn("throw ('Dependency security audit failed. ' + ($findings -join ' | '))",s)

    def test_every_packaging_option_has_its_flag(self):
        # A merge once left 'chrome_setup' without '--hidden-import' before it, and
        # PyInstaller read it as a second program, failing the Windows build.
        import re
        s=Path(__file__).with_name('build-windows.ps1').read_text()
        problems=[]
        for line in s.splitlines():
            if "'--hidden-import'" not in line:continue
            items=re.findall(r"'([^']*)'",line)
            for i,item in enumerate(items):
                if item.startswith('--'):continue
                if i==0 or not items[i-1].startswith('--'):problems.append((item,line.strip()[:80]))
        self.assertEqual(problems,[])

    def test_builder_uses_launcher_and_current_data_syntax(self):
        s=Path(__file__).with_name('build-windows.ps1').read_text()
        self.assertIn("'build_packaging.py'",s)
        self.assertNotIn("-ArgumentList (@('-m', 'PyInstaller')",s)
        self.assertIn("'START HERE - Veteran.txt:.'",s)
        self.assertIn("'model-parakeet:model-parakeet'",s)

    def test_documents_test_never_opens_real_explorer_window(self):
        # The behavioral folder-navigation check must stub the external side effect.
        s=Path(__file__).with_name('smoke_tests.py').read_text()
        self.assertIn("patch.object(companion.os, 'startfile', create=True)",s)

    def test_real_child_process_preserves_filename_with_spaces(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='Packaging user ') as folder:
            root=Path(folder)
            module=root/'PyInstaller';module.mkdir()
            (module/'__init__.py').write_text('')
            (module/'__main__.py').write_text('import sys,json; print(json.dumps(sys.argv[1:]))')
            args=['--add-data','START HERE - Veteran.txt:.','--add-data','model-parakeet:model-parakeet']
            options=root/'options.json';options.write_text(json.dumps(args))
            out=root/'stdout.txt';err=root/'stderr.txt'
            with patch.dict(os.environ, {'PYTHONPATH':str(root)}):
                self.assertEqual(package(options,out,err),0)
            self.assertEqual(json.loads(out.read_text()),args+['companion.py'])


import os
from unittest.mock import patch


class PackagedCheckCleanupTests(unittest.TestCase):
    """0.2.160: the packaged bookmark check passed, then failed the build because
    embedded Edge still held a file in its temporary profile for a moment."""
    def test_a_briefly_locked_profile_is_removed_once_free(self):
        import shutil, tempfile
        import packaged_browser_check
        folder = tempfile.mkdtemp(); open(os.path.join(folder, 'BrowserMetrics.pma'), 'w').close()
        real, tries = shutil.rmtree, []
        def busy_then_free(path):
            tries.append(path)
            if len(tries) < 3: raise PermissionError('[WinError 5] Access is denied')
            real(path)
        with patch('shutil.rmtree', busy_then_free):
            self.assertTrue(packaged_browser_check.remove_folder(folder, pause=0))
        self.assertFalse(os.path.exists(folder)); self.assertEqual(len(tries), 3)

    def test_a_profile_that_stays_locked_does_not_fail_the_check(self):
        import shutil, tempfile
        import packaged_browser_check
        folder = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, folder, True)
        def locked(path): raise PermissionError('[WinError 5] Access is denied')
        with patch('shutil.rmtree', locked):
            self.assertFalse(packaged_browser_check.remove_folder(folder, attempts=2, pause=0))


class RealWindowTestRunTests(unittest.TestCase):
    """Real-window key tests run in their own process after the release tests."""
    def test_window_tests_skip_in_the_shared_run_and_run_on_their_own(self):
        import real_window_tests
        class Sample(unittest.TestCase):
            def test_key(self): pass
        with patch.object(real_window_tests.sys, 'platform', 'win32'):
            with patch.dict(os.environ, {real_window_tests.APART: '1'}):
                self.assertTrue(getattr(real_window_tests.real_window(type('A', (Sample,), {})), '__unittest_skip__', False))
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(real_window_tests.APART, None)
                self.assertFalse(getattr(real_window_tests.real_window(type('B', (Sample,), {})), '__unittest_skip__', False))
        script = (Path(__file__).parent / 'build-windows.ps1').read_text(encoding='utf-8')
        for name in ('test_keyboard_text.WindowKeyboardTests', 'test_input_parity.WindowScreenKeyTests', 'test_time_date_windows.WindowClockKeyTests', 'test_alt_keys_window.AltKeyWindowTests'):
            self.assertIn("'" + name + "'", script)
        self.assertLess(script.index("VOICE_COMPANION_WINDOW_TESTS_APART = '1'"), script.index('$releaseTests = Start-Process'))


class WindowCallbackErrorTests(unittest.TestCase):
    """A key handler error must not keep the window's Tk objects alive in
    sys.last_traceback, where another thread would free them (Tcl_AsyncDelete)."""
    def test_key_handler_errors_are_kept_as_text(self):
        import ast
        source = (Path(__file__).parent / 'app_window.py').read_text(encoding='utf-8')
        self.assertIn('root.report_callback_exception=callback_error', source)
        tree = ast.parse(source)
        handler = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'callback_error')
        names = {node.attr for node in ast.walk(handler) if isinstance(node, ast.Attribute)}
        self.assertFalse(names & {'last_traceback', 'last_value', 'last_type', 'last_exc'})



class OwnLauncherTests(unittest.TestCase):
    """Antivirus false alarms (Will, October 2026): the build compiles PyInstaller's launcher
    itself instead of using the stock one that malware also ships."""
    def test_the_build_compiles_pyinstallers_launcher_from_source(self):
        script = (Path(__file__).parent / 'build-windows.ps1').read_text(encoding='utf-8')
        compile_at = script.index("$env:PYINSTALLER_COMPILE_BOOTLOADER = '1'")
        self.assertIn("--no-binary pyinstaller 'pyinstaller==6.22.3'", script[compile_at:compile_at + 400])
        self.assertLess(compile_at, script.index('function Invoke-PyInstaller'))
        self.assertIn("throw 'PyInstaller could not build its own launcher", script)

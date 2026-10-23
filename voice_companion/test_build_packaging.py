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
        # A long annotation keeps its start, where the failed test names are.
        # Keeping only the end once hid two of three failed tests.
        self.assertNotIn("$clean = $clean.Substring($clean.Length - 3500)",s)
        self.assertIn("$clean.Substring(0, 1500)",s)
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

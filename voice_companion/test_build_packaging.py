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

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import companion
from guide_files import GUIDE_NAMES, guide_sources, publish_guides

class GuideFileTests(unittest.TestCase):
    def test_all_formats_publish_update_and_preserve_other_user_files(self):
        with tempfile.TemporaryDirectory(prefix='User name ') as directory:
            root=Path(directory);source=root/'Program Files';source.mkdir();docs=root/'OneDrive'/'Documents'/'Voice Companion'
            for name in GUIDE_NAMES:(source/name).write_bytes(('current '+name).encode())
            target=docs/'User Guides';target.mkdir(parents=True)
            (target/'personal note.txt').write_text('keep me')
            (target/GUIDE_NAMES[0]).write_text('older guide')
            self.assertEqual(publish_guides(docs,[source]),target)
            for name in GUIDE_NAMES:self.assertEqual((target/name).read_bytes(),(source/name).read_bytes())
            self.assertEqual((target/'personal note.txt').read_text(),'keep me')
            with patch('guide_files.os.replace') as replace:
                publish_guides(docs,[source]);replace.assert_not_called()
            (source/GUIDE_NAMES[0]).write_text('updated build')
            publish_guides(docs,[source]);self.assertEqual((target/GUIDE_NAMES[0]).read_text(),'updated build')

    def test_missing_format_fails_before_creating_partial_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            (source/GUIDE_NAMES[0]).write_text('text')
            with self.assertRaises(FileNotFoundError):publish_guides(root/'Documents',[source])
            self.assertFalse((root/'Documents').exists())

    def test_sources_support_frozen_bundle_and_executable_folder(self):
        import guide_files
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);installed=root/'installed';bundle=root/'bundle';installed.mkdir();bundle.mkdir()
            for i,name in enumerate(GUIDE_NAMES):((installed if i%2 else bundle)/name).write_text(name)
            import json, hashlib
            (bundle/guide_files.MANIFEST_NAME).write_text(json.dumps({'version':'1.2.3-test','files':{name:hashlib.sha256(name.encode()).hexdigest() for name in GUIDE_NAMES}}))
            with patch.object(guide_files.sys,'frozen' ,True,create=True),patch.object(guide_files.sys,'executable',str(installed/'VoiceCompanion.exe')),patch.object(guide_files.sys,'_MEIPASS',str(bundle),create=True):
                self.assertEqual(set(guide_sources()),set(GUIDE_NAMES))

    def test_folder_commands_preserve_every_app_area(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'User Guides'
            with patch('guide_files.publish_guides',return_value=folder) as publish,patch.object(companion.os,'startfile',create=True),patch.object(companion,'documents_folder',return_value=Path(directory)),patch.object(companion,'speak'),patch.object(companion,'interrupt_speech'),patch.object(companion,'voice_menu',return_value=False),patch.object(companion,'change_speech_setting',return_value=False):
                for mode in ('awake','email_draft','document','help','tutorial'):
                    for phrase in ('open user guides','open guides folder','where are my user guides'):
                        self.assertEqual(companion.handle(phrase,mode),mode)
                self.assertEqual(publish.call_count,15)

    def test_installer_includes_all_formats_and_accessible_shortcuts(self):
        root=Path(__file__).parent;installer=(root/'VoiceCompanion.iss').read_text();builder=(root/'build-windows.ps1').read_text()
        for name in GUIDE_NAMES:
            self.assertIn("'"+name+":.'",builder)
            line=next(line for line in installer.splitlines() if line.startswith('Source:') and name in line)
            self.assertIn('{userdocs}\\Voice Companion\\User Guides',line)
        self.assertIn('Name: "{userdesktop}\\Voice Companion User Guides"',installer)
        self.assertIn('Name: "{group}\\Voice Companion User Guides"',installer)
        self.assertIn("'test_guide_files'",builder)


    def test_current_manifest_chooses_matching_bundle_and_repairs_all_stale_copies(self):
        from guide_files import write_manifest
        import json
        with tempfile.TemporaryDirectory(prefix='Redirected Documents ') as folder:
            root=Path(folder);fresh=root/'bundle';stale=root/'installed';fresh.mkdir();stale.mkdir()
            for name in GUIDE_NAMES:
                (fresh/name).write_bytes(('new '+name).encode())
                (stale/name).write_bytes(('old '+name).encode())
            write_manifest(fresh,'1.2.3-test');write_manifest(stale,'1.2.2-test')
            docs=root/'OneDrive'/'Documents'/'Voice Companion'
            target=publish_guides(docs,[stale,fresh],expected_version='1.2.3-test')
            for name in GUIDE_NAMES:self.assertEqual((target/name).read_bytes(),(fresh/name).read_bytes())
            receipt=json.loads((target/'documentation-version.json').read_text())
            self.assertEqual(receipt['version'],'1.2.3-test');self.assertTrue(receipt['verified'])
            (target/GUIDE_NAMES[1]).write_text('stale HTML after update')
            publish_guides(docs,[stale,fresh],expected_version='1.2.3-test')
            self.assertEqual((target/GUIDE_NAMES[1]).read_bytes(),(fresh/GUIDE_NAMES[1]).read_bytes())

    def test_missing_version_or_damaged_source_never_certifies_guides_as_current(self):
        from guide_files import write_manifest
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir()
            for name in GUIDE_NAMES:(source/name).write_bytes(b'current document')
            write_manifest(source,'1.2.3-test')
            with self.assertRaises(ValueError):publish_guides(root/'docs',[source],expected_version='1.2.4-test')
            self.assertFalse((root/'docs').exists())
            (source/GUIDE_NAMES[-1]).write_bytes(b'damaged file')
            with self.assertRaises(FileNotFoundError):publish_guides(root/'docs',[source],expected_version='1.2.3-test')
            self.assertFalse((root/'docs').exists())

    def test_silent_setup_and_installed_checks_refresh_guides_without_audio_startup(self):
        root=Path(__file__).parent
        installer=(root/'VoiceCompanion.iss').read_text()
        self.assertIn('Parameters: "--refresh-guides"; Flags: runhidden',installer)
        self.assertNotIn('skipifsilent',next(l for l in installer.splitlines() if 'Parameters: "--refresh-guides"' in l))
        builder=(root/'build-windows.ps1').read_text()
        self.assertGreater(builder.index('& $installedDiagnostics --refresh-guides'),builder.index('$installation.WaitForExit()'))
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(companion.sys,'argv',['companion','--refresh-guides']),patch('guide_files.publish_guides',return_value=Path(folder)) as publish,patch.object(companion,'documents_folder',return_value=Path(folder)),patch.object(companion,'speak') as speech:
                self.assertEqual(companion.main(),0)
                publish.assert_called_once_with(Path(folder),expected_version=companion.APP_VERSION)
                speech.assert_not_called()

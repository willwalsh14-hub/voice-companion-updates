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
            with patch.object(guide_files.sys,'frozen',True,create=True),patch.object(guide_files.sys,'executable',str(installed/'VoiceCompanion.exe')),patch.object(guide_files.sys,'_MEIPASS',str(bundle),create=True):
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

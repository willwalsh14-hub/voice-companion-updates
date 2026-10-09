import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import ai_voice_settings as settings

class SettingsTests(unittest.TestCase):
    def test_key_saved_as_protected_bytes_and_removable(self):
        crypto=Mock()
        crypto.CryptProtectData.return_value=b'protected-by-windows'
        crypto.CryptUnprotectData.return_value=('description',json.dumps({'key':'secret','voice':'coral'}).encode())
        with tempfile.TemporaryDirectory() as folder, patch.dict('sys.modules', {'win32crypt':crypto}):
            settings.save(folder,'secret','coral',True)
            saved=Path(folder)/'ai-voice.account'
            self.assertEqual(saved.read_bytes(),b'protected-by-windows')
            self.assertNotIn(b'secret',saved.read_bytes())
            self.assertEqual(settings.load(folder)['key'],'secret')
            settings.remove(folder)
            self.assertFalse(saved.exists())

    def test_consent_and_input_validation_prevent_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            for key,voice,consent in [('secret','coral',False),('','coral',True),('secret','unknown',True)]:
                with self.assertRaises(ValueError): settings.save(folder,key,voice,consent)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_setup_voice_command_posts_ui_action_without_changing_mode(self):
        import companion
        window=Mock()
        with patch.object(companion,'APP_WINDOW',window),patch.object(companion,'speak'):
            self.assertEqual(companion.handle('set up AI voice','document'),'document')
            window.setup_ai_voice.assert_called_once()

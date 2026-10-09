import unittest
from unittest.mock import Mock,patch
import companion

class VoiceChoicesTests(unittest.TestCase):
    def setUp(self):
        self.speech=Mock()
        self.token=Mock()
        self.token.GetDescription.return_value='Windows voice'
        self.speech.Voice=self.token
        self.speech.GetVoices.return_value=Mock(Count=1,Item=lambda i:self.token)
        self.ai=Mock(enabled=False)
        self.patches=[patch.object(companion,'voice',self.speech,create=True),
            patch.object(companion,'AI_SPEECH',self.ai),patch.object(companion,'TEXT_MODE',False),
            patch.object(companion,'AI_VOICE_NAME','coral'),patch.object(companion,'VOICE_PICK_INDEX',None),
            patch.object(companion,'VOICE_PICK_ORIGINAL',None),patch.object(companion,'SYNTH_PICK_INDEX',None),
            patch.object(companion,'VOICE_PICK_ENGINE',None),patch.object(companion,'ESPEAK_SPEECH',None),patch.object(companion,'VOICE_PICK_CONFIRM',False),
            patch.object(companion,'save_speech_settings')]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def test_ai_preview_select_and_save_through_existing_commands(self):
        companion.handle('list voices','awake')
        self.assertFalse(self.ai.enabled)
        companion.handle('next','awake')
        companion.handle('okay','awake')
        self.assertTrue(self.ai.enabled)
        self.assertEqual(companion.AI_VOICE_NAME,'alloy')
        self.assertIn('AI voice Alloy',self.ai.speak.call_args.args[0])
        companion.save_speech_settings.assert_not_called()
        companion.handle('okay','awake')
        companion.save_speech_settings.assert_called_once()
        self.assertIsNone(companion.VOICE_PICK_INDEX)

    def test_cancel_restores_previous_ai_voice(self):
        self.ai.enabled=True
        companion.handle('list voices','awake')
        self.assertTrue(self.ai.enabled)
        companion.handle('okay','awake')
        self.assertFalse(self.ai.enabled)
        companion.handle('next voice','awake')
        companion.handle('cancel voice','awake')
        self.assertTrue(self.ai.enabled)
        self.assertEqual(companion.AI_VOICE_NAME,'coral')
        companion.save_speech_settings.assert_not_called()

    def test_unconfigured_ai_is_not_in_list(self):
        with patch.object(companion,'AI_SPEECH',None):
            self.assertEqual(len(companion.voice_choices()),1)

    def test_picker_stays_available_in_radio_mode(self):
        companion.handle('list voices','media')
        companion.handle('next','media')
        companion.handle('that one','media')
        self.assertTrue(self.ai.enabled)
        companion.handle('okay','media')
        companion.save_speech_settings.assert_called_once()

    def test_switching_by_windows_name_disables_ai(self):
        self.ai.enabled=True
        companion.handle('use voice Windows voice','awake')
        self.assertFalse(self.ai.enabled)

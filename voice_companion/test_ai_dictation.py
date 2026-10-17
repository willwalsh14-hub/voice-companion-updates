import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_dictation import dictation_scope, prepare_dictation
from document_editor import VoiceDocument
from email_draft import VoiceEmail
from web_assistant import _voice_value
from settings_model import SettingsSession, DEFAULTS, fields
from privacy_controls import cloud_recognition_allowed


class AIDictationTests(unittest.TestCase):
    def test_local_cleanup_and_meaning_are_conservative(self):
        with dictation_scope('ai-based'):
            self.assertEqual(prepare_dictation('I, um, I w-w-want to go, uh, tomorrow.'),
                             'I want to go tomorrow.')
            self.assertEqual(prepare_dictation('Um. Uh.'), '')
            for text in ('I had had enough.', 'That that happened is clear.',
                         'It is very, very good.', 'No, no, do not do that.',
                         'I like this, you know.', 'The total is 22.50.',
                         'Say "um" in the example.', 'uh-huh', 'no-no', 'ha-ha', 'Um@example.com', '... Wait.'):
                self.assertEqual(prepare_dictation(text), text)
            self.assertEqual(prepare_dictation('I, um, need 22.50 dollars.'),'I need 22.50 dollars.')
            self.assertEqual(prepare_dictation('I I need a new line here period'),
                             'I need a\nhere.')

    def test_old_school_typed_and_scope_reset(self):
        text='I I, um, want this.'
        self.assertEqual(prepare_dictation(text), text)
        with dictation_scope('ai-based', typed=True):
            self.assertEqual(prepare_dictation(text), text)
        with self.assertRaises(ValueError):
            with dictation_scope('ai-based'):
                raise ValueError()
        self.assertEqual(prepare_dictation(text), text)

    def test_shared_insertion_and_literal_escape_and_undo(self):
        with tempfile.TemporaryDirectory() as folder, dictation_scope('ai-based'):
            doc=VoiceDocument(Path(folder)/'doc');doc.dictating=True
            doc.process('I I, um, want this.')
            self.assertEqual(doc.paragraphs[0].text, 'I want this.')
            doc.process('type literally I I um')
            self.assertTrue(doc.paragraphs[0].text.endswith('I I um'))
            self.assertIn('Undid',doc.process('undo'))
            self.assertEqual(doc.paragraphs[0].text, 'I want this.')
            email=VoiceEmail(Path(folder)/'email')
            email.process('subject is I I, um, need help')
            self.assertEqual(email.subject, 'I need help')
            self.assertIn('one line',email.process('subject is Hello new line private'))
            self.assertEqual(email.subject, 'I need help')
            email.process('I I, uh, will call you.')
            self.assertEqual(email.paragraphs[0].text,'I will call you.')
            self.assertEqual(_voice_value({'type':'textarea'},'I I, um, need help.'),'I need help.')
            self.assertEqual(_voice_value({'type':'password'},'I I um'),'I I um')
            self.assertEqual(_voice_value({'type':'text','label':'User name'},'I I um'),'I I')
            self.assertEqual(_voice_value({'type':'text'},'I I um',literal=True),'I I um')

    def test_setting_default_aliases_and_offline_privacy(self):
        self.assertEqual(DEFAULTS['dictation_type'],'old school')
        session=SettingsSession({}, {})
        self.assertIn('dictation_type',[f.key for f in fields('Input options',{})])
        key,value=session.voice_setting('dictation type ai based')
        session.set(key,value);self.assertEqual(session.values[key],'ai-based')
        session.cancel();self.assertEqual(session.values[key],'old school')
        self.assertFalse(cloud_recognition_allowed('document',{'dictation_type':'ai-based','cloud_consent':True}))
        self.assertTrue(cloud_recognition_allowed('document',{'dictation_type':'old school','cloud_consent':True}))

    def test_real_dispatch_scopes_note_cleanup_without_changing_commands(self):
        import companion
        with patch.dict(companion.PREFERENCES,{'dictation_type':'ai-based'}), \
             patch.object(companion,'note_buffer',[]), \
             patch.object(companion,'sync_app_context'), \
             patch.object(companion,'flush_keyboard_edits'), \
             patch.object(companion,'speak'), patch.object(companion,'speak_prompt'):
            self.assertEqual(companion.handle('I I, um, will call.','note'),'note')
            self.assertEqual(companion.note_buffer,['I will call.'])
            self.assertEqual(companion.handle('type literally I I um','note'),'note')
            self.assertEqual(companion.note_buffer[-1],'I I um')


if __name__=='__main__':unittest.main()

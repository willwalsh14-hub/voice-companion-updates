import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from settings_model import SettingsSession,DEFAULTS,CATEGORIES,fields,prompt_text,keyboard_request
import companion

class SettingsTests(unittest.TestCase):
    def setUp(self):self.context={'engines':('windows','espeak','ai'),'windows_voices':('Microsoft David',),'espeak_voices':('eSpeak UK English',),'ai_voices':('coral','nova'),'accounts':(),'podcasts':('1. First','2. Second'),'podcast_limits':{'1. First':'manual','2. Second':'all'}}
    def test_cancel_discards_all_staged_changes(self):
        original=DEFAULTS|{'volume':'100'};session=SettingsSession(original,self.context)
        session.set('verbosity','low');session.set('duck_audio','off');session.set('volume','50');session.cancel()
        self.assertEqual(session.values,original)
        self.assertEqual(original['verbosity'],'high')
    def test_voice_commands_share_the_same_validated_settings(self):
        session=SettingsSession(DEFAULTS|{'rate':'0','volume':'100'},self.context)
        for command,key,value in [('verbosity medium','verbosity','medium'),('punctuation most','punctuation','most'),('speed for','rate','4'),('softer','volume','90'),('audio ducking off','duck_audio','off'),('use AI voice','engine','ai'),('default font Times New Roman','document_font','Times New Roman')]:
            self.assertEqual(session.voice_setting(command),(key,value));session.set(key,value)
        with self.assertRaises(ValueError):session.set('verbosity','maximum')
        with self.assertRaises(ValueError):session.set('volume','150')
    def test_podcast_edits_survive_switching_between_subscriptions(self):
        session=SettingsSession(DEFAULTS,self.context)
        session.set('podcast_feed','1. First');session.set('podcast_limit','5')
        session.set('podcast_feed','2. Second');session.set('podcast_limit','10')
        session.set('podcast_feed','1. First')
        self.assertEqual(session.values['podcast_limit'],'5')
        self.assertEqual(session.values['podcast_limits'],{'1. First':'5','2. Second':'10'})
    def test_secret_values_are_not_in_feedback(self):
        session=SettingsSession(DEFAULTS,self.context)
        self.assertNotIn('private-value',session.set('api_key','private-value'))
    def test_new_document_defaults_do_not_insert_command_words(self):
        from document_editor import VoiceDocument
        from app_settings import apply_document_defaults
        for spacing in ('single','one and a half','double'):
            with tempfile.TemporaryDirectory() as folder:
                doc=VoiceDocument(Path(folder))
                app=Mock(PREFERENCES=DEFAULTS|{'document_spacing':spacing})
                apply_document_defaults(app,doc)
                self.assertTrue(all(not p.text for p in doc.paragraphs))
    def test_candidate_keyboard_confirmation_and_cancel_preserve_document(self):
        editor=Mock(selection_candidates=[1,2],replacement_candidates=[],pending_spacing=False)
        with patch.object(companion,'document',editor):
            self.assertEqual(companion.navigation_key('Enter','document'),'ok')
            self.assertEqual(companion.navigation_key('Escape','document'),'cancel selection')
            self.assertEqual(companion.navigation_key('Down','document'),'next')
    def test_settings_actions_dispatch_without_dictating_into_a_document(self):
        hub=Mock()
        with patch.object(companion,'media',return_value=hub),patch.object(companion,'speak'),patch.object(companion,'MEDIA_SECTION','radio'):
            self.assertEqual(companion.settings_action('podcast_subscriptions','document'),'media')
            hub.command.assert_called_once_with('list subscriptions',section='podcast')
    def test_verbosity_does_not_change_document_or_email_reading(self):
        text='This paragraph says Say next or previous. It must remain intact.'
        for level in ('high','medium','low'):
            with patch.object(companion,'VERBOSITY',level),patch.object(companion,'TEXT_MODE',True),patch.object(companion,'APP_WINDOW') as window:
                companion.speak(text);window.show.assert_called_once_with(text)
        high='Main menu. What would you like to do? Say next or previous to move through the items, and okay, confirm, confirm that, or that one to open.'
        self.assertGreater(len(prompt_text(high,'high')),len(prompt_text(high,'medium')))
        self.assertGreater(len(prompt_text(high,'medium')),len(prompt_text(high,'low')))
    def test_verbosity_and_most_punctuation_commands_persist(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(companion,'APP',Path(folder)),patch.object(companion,'VERBOSITY','high'),patch.object(companion,'PREFERENCES',dict(DEFAULTS)),patch.object(companion,'PUNCTUATION_LEVEL','some'),patch.object(companion,'TEXT_MODE',True),patch.object(companion,'speak'):
            self.assertEqual(companion.handle('verbosity medium','awake'),'awake')
            self.assertEqual(json.loads((Path(folder)/'speech-settings.json').read_text())['verbosity'],'medium')
            companion.handle('punctuation most','awake')
            self.assertEqual(json.loads((Path(folder)/'speech-settings.json').read_text())['punctuation'],'most')
    def test_punctuation_levels_are_distinct(self):
        with patch.object(companion,'PUNCTUATION_LEVEL','most'):most=companion.punctuation_for_speech('Hello, world.')
        with patch.object(companion,'PUNCTUATION_LEVEL','all'):all_=companion.punctuation_for_speech('Hello, world.')
        self.assertIn('comma',most);self.assertNotIn('period',most);self.assertIn('period',all_)
    def test_contextual_keyboard_navigation(self):
        for mode in ('awake','mailbox','media','help','search'):
            self.assertEqual(keyboard_request('Down',mode),'next');self.assertEqual(keyboard_request('Up',mode),'previous');self.assertEqual(keyboard_request('Enter',mode),'ok')
        self.assertEqual(keyboard_request('Escape','email_draft'),'cancel')
        self.assertEqual(keyboard_request('Escape','update_offer'),'no')
        self.assertEqual(keyboard_request('Escape','update_download'),'cancel update')
        self.assertEqual(keyboard_request('Escape','document'),'leave document')
        self.assertEqual(keyboard_request('Enter','sleep'),'wake up')
    def test_keyboard_enter_opens_highlighted_mail_in_both_views(self):
        mailbox=Mock(folder_picker=None,pending=None)
        with patch.object(companion,'mail_session',mailbox):
            for view in ('folder','message'):
                mailbox.view=view
                self.assertEqual(companion.navigation_key('Enter','mailbox'),'open current message')
        self.assertIsNone(companion.navigation_key('Down','settings'))
        self.assertEqual(companion.navigation_key('Escape','settings'),'cancel settings')

    def test_all_categories_have_settings_or_actions(self):
        self.assertIn('Email',CATEGORIES);self.assertIn('Documents',CATEGORIES);self.assertIn('Radio',CATEGORIES)
        for category in CATEGORIES:self.assertTrue(fields(category,self.context))

if __name__=='__main__':unittest.main()


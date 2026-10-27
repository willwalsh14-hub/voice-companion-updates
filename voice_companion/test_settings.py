import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from settings_model import SettingsSession,DEFAULTS,CATEGORIES,fields,prompt_text,keyboard_request
import companion

class SettingsTests(unittest.TestCase):
    def test_staged_sapi_sample_does_not_change_saved_speech(self):
        from app_settings import preview_settings
        m=Mock(TEXT_MODE=False,SETTINGS_PREVIEW_SPEECH=None)
        m.voice.Voice.Id='original';selected=Mock(Id='sample')
        m.voice_choices.return_value=[('windows','Sample voice',selected)]
        values={'engine':'SAPI','windows_voice':'Sample voice','rate':'7','volume':'60','pitch':'2'}
        preview_settings(m,values,'Sample')
        m.KEYBOARD_SPEECH.preview.assert_called_once_with('Sample',('sample',7,60,None,2))
        self.assertEqual(m.voice.Voice.Id,'original');m.save_speech_settings.assert_not_called();m.choose_voice.assert_not_called()
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
    def test_mail_enter_opens_selected_message_instead_of_continuing_old_reading(self):
        mail=Mock(folder_picker=None,folder_choice=None,pending=None)
        with patch.object(companion,'mail_session',mail):
            self.assertEqual(companion.navigation_key('Enter','mailbox'),'open current message')
    def test_settings_actions_dispatch_without_dictating_into_a_document(self):
        hub=Mock(shows=[],show_index=None,station_choices=[],browsing_episodes=False,preset_delete_pending=None)
        hub.command.return_value='No podcast subscriptions.'
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
        mailbox=Mock(folder_picker=None,folder_choice=None,pending=None)
        with patch.object(companion,'mail_session',mailbox):
            for view in ('folder','message'):
                mailbox.view=view
                self.assertEqual(companion.navigation_key('Enter','mailbox'),'open current message')
        self.assertIsNone(companion.navigation_key('Down','settings'))
        self.assertEqual(companion.navigation_key('Escape','settings'),'cancel settings')

    def test_all_categories_have_settings_or_actions(self):
        self.assertIn('Email options',CATEGORIES);self.assertIn('Word processor options',CATEGORIES);self.assertIn('Radio options',CATEGORIES)
        for category in CATEGORIES:self.assertTrue(fields(category,self.context))

class OptionsSaveReturnTests(unittest.TestCase):
    def test_success_announces_once_and_returns_to_each_task(self):
        import companion as app
        from unittest.mock import Mock,patch
        for destination in ('document','email_draft','web','note','awake'):
            panel=Mock()
            with patch.object(app,'SETTINGS_PANEL',panel),patch.object(app,'SETTINGS_RETURN_MODE',destination),patch.object(app,'apply_settings') as apply,patch.object(app,'speak') as speech,patch.object(app,'sync_app_context') as sync,patch('app_settings.stop_preview'):
                self.assertEqual(app.save_options({'verbosity':'low'},'settings'),destination)
                apply.assert_called_once_with({'verbosity':'low'})
                panel.accepted.assert_called_once()
                speech.assert_called_once_with('Options saved. '+{'document':'Back to document.','email_draft':'Back to email draft.','web':'Back to web page.','note':'Back to your note.','awake':'Main menu.'}[destination])
                sync.assert_called_once_with(destination,True)

    def test_success_drops_old_field_notices_before_save_confirmation(self):
        import companion as app
        from queue import Queue
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        window=SimpleNamespace(settings_notices=Queue())
        window.settings_notices.put('Verbosity low')
        def announce(text):
            self.assertEqual(text,'Options saved. Main menu.')
            self.assertTrue(window.settings_notices.empty())
        with patch.object(app,'APP_WINDOW',window),patch.object(app,'SETTINGS_PANEL',Mock()),patch.object(app,'apply_settings'),patch.object(app,'speak',side_effect=announce),patch.object(app,'sync_app_context'),patch('app_settings.stop_preview'):
            app.save_options({},'settings')

    def test_failed_save_does_not_close_or_announce_success(self):
        import companion as app
        from unittest.mock import Mock,patch
        panel=Mock()
        with patch.object(app,'SETTINGS_PANEL',panel),patch.object(app,'apply_settings',side_effect=ValueError('Invalid setting')),patch.object(app,'speak') as speech,patch('app_settings.stop_preview'):
            with self.assertRaises(ValueError):app.save_options({},'settings')
            panel.accepted.assert_not_called();speech.assert_not_called()

    def test_sleep_retains_previous_task_and_web_options_have_no_favorites_action(self):
        import companion as app
        from unittest.mock import Mock,patch
        self.assertNotIn('web_favorites',{f.key for f in fields('Web browser',{})})
        with patch.object(app,'SETTINGS_PANEL',Mock()),patch.object(app,'SETTINGS_RETURN_MODE','document'),patch.object(app,'SLEEP_RETURN_MODE','settings'),patch.object(app,'apply_settings'),patch.object(app,'speak'),patch.object(app,'sync_app_context'),patch('app_settings.stop_preview'):
            self.assertEqual(app.save_options({},'sleep'),'sleep')
            self.assertEqual(app.SLEEP_RETURN_MODE,'document')

if __name__=='__main__':unittest.main()



class CapitalPreferenceTests(unittest.TestCase):
    def test_each_capital_choice_saves_and_reloads_without_changing_other_preferences(self):
        from app_settings import save_preferences
        from types import SimpleNamespace
        field=next(f for f in fields('Verbosity',{}) if f.key=='capital_letters')
        self.assertEqual(field.label,'When encountering capital letters')
        self.assertEqual(field.choices,('raise pitch','lower pitch','play a sound','do nothing'))
        with tempfile.TemporaryDirectory() as folder:
            app=SimpleNamespace(APP=Path(folder),PREFERENCES=dict(DEFAULTS))
            for value in field.choices:
                session=SettingsSession(app.PREFERENCES,{})
                session.set('capital_letters',value);app.PREFERENCES=session.values;save_preferences(app)
                reloaded=SettingsSession(DEFAULTS|json.loads((app.APP/'preferences.json').read_text()),{})
                self.assertEqual(reloaded.values['capital_letters'],value)
                self.assertEqual(reloaded.values['typing_echo'],DEFAULTS['typing_echo'])
                self.assertEqual(reloaded.voice_setting('when encountering capital letters '+value),('capital_letters',value))
        with self.assertRaises(ValueError):session.set('capital_letters','invalid')

    def test_old_capital_choices_migrate_to_the_new_names(self):
        for old,new in (('with a sound','play a sound'),('with a higher pitch','raise pitch'),('with a lower pitch','lower pitch')):
            session=SettingsSession({'capital_letters':old},{})
            self.assertEqual(session.values['capital_letters'],new)
            session.set('capital_letters',old)
            self.assertEqual(session.values['capital_letters'],new)

    def test_spoken_setting_is_global_and_updates_immediate_feedback(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(companion,'APP',Path(folder)),patch.object(companion,'PREFERENCES',dict(DEFAULTS)),patch.object(companion,'KEYBOARD_SPEECH',Mock(problem=None)) as worker,patch.object(companion,'speak'):
            for mode,value in zip(('web','document','help','awake'),('raise pitch','lower pitch','play a sound','do nothing')):
                self.assertEqual(companion.handle('capital letters '+value,mode),mode)
                self.assertEqual(worker.capital_letters,value)
                self.assertEqual(json.loads((Path(folder)/'preferences.json').read_text())['capital_letters'],value)

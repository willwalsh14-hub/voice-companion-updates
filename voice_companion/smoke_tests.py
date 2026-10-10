"""Release checks for local work and potentially consequential voice actions."""
import tempfile
import json
import unittest
import sys
import zipfile
import io
import contextlib
import time
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import companion
from document_editor import VoiceDocument
from email_draft import VoiceEmail
from audio_resample import PCM16Resampler, stereo_to_mono


class VoiceCompanionSmokeTests(unittest.TestCase):
    def test_offline_partial_silence_is_early_but_stop_remains_unambiguous(self):
        class Recognizer:
            def __init__(self, value): self.value=value; self.resets=0
            def PartialResult(self): return json.dumps({'partial':self.value})
            def Reset(self): self.resets+=1
        with patch.object(companion, 'control_speech') as silence:
            for phrase in ('be quiet', 'shut up', 'shut the hell up', 'shut the fuck up'):
                recognizer=Recognizer(phrase)
                audio=bytearray(b'dictation')
                self.assertTrue(companion.fast_offline_silence(recognizer,audio))
                self.assertEqual((recognizer.resets,audio), (1,bytearray()))
            self.assertEqual(silence.call_count,4)
            self.assertFalse(companion.fast_offline_silence(Recognizer('stop'),bytearray(b'audio')))

    def test_custom_spacing_confirmation_reaches_document_not_voice_picker(self):
        mode = companion.handle('create a document', 'awake')
        companion.handle('A line of text', mode)
        companion.handle('custom spacing', mode)
        companion.handle('one point seven five', mode)
        self.assertEqual(companion.document.paragraphs[0].line_spacing_twips, 0)
        with patch.object(companion, 'speak') as spoken:
            self.assertEqual(companion.handle('confirm that', mode), 'document')
        self.assertIn('Set 1.75', spoken.call_args.args[0])
        self.assertEqual(companion.document.paragraphs[0].line_spacing_twips, 420)

    def test_packaged_google_registration_is_detected_without_user_setup(self):
        import mail_accounts
        with tempfile.TemporaryDirectory() as folder:
            installation = Path(folder) / 'installed'
            installation.mkdir()
            (installation / 'google-client.json').write_text('{"installed":{}}', encoding='utf-8')
            with patch.object(sys, 'frozen', True, create=True), patch.object(
                    sys, 'executable', str(installation / 'VoiceCompanion.exe')):
                config = mail_accounts.registration(Path(folder) / 'user-data')
            self.assertEqual(config['google_credentials_file'], str(installation / 'google-client.json'))

    def test_setup_keeps_upgrade_identity_and_location(self):
        setup = (Path(__file__).parent / 'VoiceCompanion.iss').read_text(encoding='utf-8')
        self.assertIn('AppId={{9DFB48ED-1D99-4230-A78D-FD727536AC40}', setup)
        self.assertIn('DefaultDirName={localappdata}\\Programs\\Voice Companion', setup)
        self.assertIn('PrivilegesRequired=lowest', setup)
        builder = (Path(__file__).parent / 'build-windows.ps1').read_text(encoding='utf-8')
        self.assertIn("-match '^Voice Companion " + companion.APP_VERSION.replace('.', r'\.') + "$'", builder)

    def test_quick_test_and_builder_use_short_dependency_paths(self):
        root = Path(__file__).parent
        quick = (root / 'quick-test.ps1').read_text(encoding='utf-8')
        builder = (root / 'build-windows.ps1').read_text(encoding='utf-8')
        self.assertIn("Join-Path $env:LOCALAPPDATA 'VCQuick\\py312'", quick)
        self.assertIn("Join-Path $env:LOCALAPPDATA 'VCBuild\\py312'", builder)
        self.assertNotIn("$PSScriptRoot '.quick-env", quick)
        self.assertNotIn("$PSScriptRoot '.build-env", builder)

    def test_spoken_feedback_uses_voice_output(self):
        with patch.object(companion, 'TEXT_MODE', False), patch.object(
                companion, 'voice', create=True) as speech:
            companion.speak('Heard: write an email')
            speech.Speak.assert_called_once_with('Heard: write an email', 1)

    def test_silence_and_resume_never_speak_and_keep_position(self):
        with patch.object(companion, 'TEXT_MODE', False), patch.object(
                companion, 'voice', create=True) as speech, patch.object(
                companion, 'SPEECH_PAUSED', False), patch.object(companion, 'APP_WINDOW', None):
            for phrase in ('be quiet', 'stop', 'hush up', 'shut the hell up', 'shut the fuck up'):
                self.assertEqual(companion.handle(phrase, 'mailbox'), 'mailbox')
                speech.Pause.assert_called()
                speech.Speak.assert_not_called()
                self.assertEqual(companion.handle('keep going', 'mailbox'), 'mailbox')
                speech.Resume.assert_called()
                speech.Speak.assert_not_called()

    def test_new_command_purges_old_speech(self):
        from unittest.mock import MagicMock
        session = MagicMock()
        session.process.return_value = 'Next message from Jane. Subject: Hello.'
        with patch.object(companion, 'TEXT_MODE', False), patch.object(
                companion, 'voice', create=True) as speech, patch.object(
                companion, 'speech_busy', return_value=True), patch.object(
                companion, 'mail_session', session), patch.object(companion, 'APP_WINDOW', None):
            self.assertEqual(companion.handle('next message', 'mailbox'), 'mailbox')
            speech.Speak.assert_any_call('', 3)
            speech.Speak.assert_any_call('Next message from Jane. Subject: Hello.', 1)
            session.process.assert_called_once_with('next message')

    def test_voice_list_aliases_and_mailbox_draft_return(self):
        from unittest.mock import MagicMock
        mailbox = MagicMock()
        mailbox.folder_name = 'Inbox'
        mailbox.process.return_value = 'Back to Inbox.'
        with patch.object(companion, 'TEXT_MODE', False), patch.object(
                companion, 'voice', create=True) as speech, patch.object(
                companion, 'APP_WINDOW', None), patch.object(
                companion, 'mail_session', mailbox):
            speech.GetVoices.return_value.Count = 1
            speech.GetVoices.return_value.Item.return_value.GetDescription.return_value = 'Microsoft Voice'
            self.assertEqual(companion.handle('voice list', 'mailbox'), 'mailbox')
            self.assertIn('Select synthesizer', str(speech.Speak.call_args_list))
            companion.handle('okay', 'mailbox')
            self.assertIn('Microsoft Voice', str(speech.Speak.call_args_list))
            companion.handle('okay', 'mailbox')
            self.assertEqual(companion.handle('new message', 'mailbox'), 'email_draft')
            self.assertIs(companion.mail_session, mailbox)
            self.assertEqual(companion.handle('start dictation', 'email_draft'), 'email_draft')
            self.assertEqual(companion.handle('stop dictation', 'email_draft'), 'email_draft')
            self.assertEqual(companion.handle('go back', 'email_draft'), 'mailbox')
            mailbox.process.assert_called_with('open inbox')

    def test_microphone_stays_live_during_speech(self):
        from unittest.mock import MagicMock
        fake_converter = MagicMock()
        fake_converter.convert.return_value = b'\x00\x01'
        with patch.object(companion, 'SPEAKING', True), patch.object(
                companion, 'MUTE_UNTIL', 0), patch.object(
                companion, 'resampler', fake_converter), patch.object(
                companion, 'input_channels', 1):
            while not companion.AUDIO.empty(): companion.AUDIO.get_nowait()
            companion.callback(b'\x00\x01', 1, None, None)
            self.assertEqual(companion.AUDIO.get_nowait(), b'\x00\x01')

    def test_speech_settings_are_saved_and_punctuation_changes(self):
        with patch.object(companion, 'TEXT_MODE', True), patch.object(
                companion, 'APP', self.root), patch.object(
                companion, 'PUNCTUATION_LEVEL', 'some'), patch.object(
                companion, 'SPEECH_RATE', -1), patch.object(companion, 'SPEECH_VOLUME', 100):
            self.assertEqual(companion.handle('Faster', 'awake'), 'awake')
            self.assertEqual(companion.handle('Quieter', 'awake'), 'awake')
            self.assertEqual(companion.handle('Punctuation all', 'awake'), 'awake')
            self.assertIn('question mark', companion.punctuation_for_speech('Ready?'))
            saved = json.loads((self.root / 'speech-settings.json').read_text())
            self.assertEqual(saved['rate'], 0)
            self.assertEqual(saved['volume'], 90)
            self.assertEqual(saved['punctuation'], 'all')

    def test_account_prompt_is_spoken_without_screen_reader(self):
        with patch.object(companion, 'TEXT_MODE', False), patch.object(
                companion, 'voice', create=True) as speech, patch.object(companion, 'APP_WINDOW', None):
            companion.handle('add account', 'awake')
            spoken = [call.args[0] for call in speech.Speak.call_args_list]
            self.assertTrue(any('Which email account type' in line for line in spoken))
        companion.account_setup = False

    def test_spoken_wake_and_punctuated_dictation(self):
        self.assertEqual(companion.WAKE, ('wake up',))
        with tempfile.TemporaryDirectory() as folder:
            doc = VoiceDocument(Path(folder))
            self.assertIn('Dictation is on', doc.process('Start dictation.'))
            self.assertIn('Added:', doc.process('This is my first sentence.'))
            self.assertIn('This is my first sentence.', doc.process('read document'))
            self.assertEqual(doc.process('Pause dictation.'), 'Dictation paused.')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old_app = companion.APP
        self.old_document = companion.document
        self.old_email = companion.email_draft
        self.old_pending = companion.pending_website
        self.old_web_session = companion.web_session
        self.old_text_mode = companion.TEXT_MODE
        self.old_account_setup = companion.account_setup
        self.old_input_mode = companion.INPUT_MODE
        self.old_voice_pick = (companion.VOICE_PICK_INDEX, companion.VOICE_PICK_ORIGINAL)
        self.old_voice_confirm = companion.VOICE_PICK_CONFIRM
        for name in ('SYNTH_PICK_INDEX', 'VOICE_PICK_ENGINE'):
            patcher = patch.object(companion, name, None)
            patcher.start(); self.addCleanup(patcher.stop)
        self.old_notes = companion.note_buffer
        companion.APP = self.root
        companion.document = None
        companion.email_draft = None
        companion.pending_website = None
        companion.web_session = None
        companion.TEXT_MODE = True
        companion.account_setup = False
        companion.INPUT_MODE = 'mixed'
        companion.VOICE_PICK_INDEX = companion.VOICE_PICK_ORIGINAL = None
        companion.VOICE_PICK_CONFIRM = False
        companion.note_buffer = []
        self.addCleanup(self.restore)

    def restore(self):
        companion.APP = self.old_app
        companion.document = self.old_document
        companion.email_draft = self.old_email
        companion.pending_website = self.old_pending
        companion.web_session = self.old_web_session
        companion.TEXT_MODE = self.old_text_mode
        companion.account_setup = self.old_account_setup
        companion.INPUT_MODE = self.old_input_mode
        companion.VOICE_PICK_INDEX, companion.VOICE_PICK_ORIGINAL = self.old_voice_pick
        companion.VOICE_PICK_CONFIRM = self.old_voice_confirm

    def test_document_formatting_commands_work_in_dictation_only(self):
        mode = companion.handle('create a document', 'awake')
        companion.handle('A paragraph', mode)
        companion.handle('dictation only', mode)
        for level in range(1, 7):
            for command in (f'heading {level}', f'apply heading {level}'):
                self.assertEqual(companion.handle(command, mode), 'document')
                self.assertEqual(companion.document.paragraphs[0].kind, f'Heading {level}')
        for command in ('set font to Arial', 'set size to 14 points', 'make this bold', 'double space'):
            self.assertEqual(companion.handle(command, mode), 'document')
        paragraph = companion.document.paragraphs[0]
        self.assertEqual((paragraph.kind, paragraph.font, paragraph.size, paragraph.bold),
                         ('Heading 6', 'Arial', 14, True))
        self.assertNotIn('apply heading', paragraph.text.lower())
        self.assertIn('A paragraph', paragraph.text)
        self.assertEqual(companion.documents_folder(), self.root / 'Documents')
        with patch.object(companion, 'speak') as speech, \
             patch.object(companion.os, 'startfile', create=True) as open_folder:
            self.assertEqual(companion.handle('open documents', mode), mode)
            self.assertIn('Documents', speech.call_args.args[0])

    def test_new_line_is_not_dictation_and_survives_word_round_trip(self):
        mode = companion.handle('create a document', 'awake')
        companion.handle('First line', mode)
        companion.handle('dictation only', mode)
        companion.handle('new line', mode)
        companion.handle('Second line', mode)
        self.assertEqual(len(companion.document.paragraphs), 1)
        self.assertEqual(companion.document.paragraphs[0].text, 'First line\nSecond line')
        reopened = VoiceDocument.open_existing(self.root / 'Documents', companion.document.title)
        self.assertEqual(reopened.paragraphs[0].text, 'First line\nSecond line')
        companion.handle('new paragraph', mode)
        self.assertEqual(len(companion.document.paragraphs), 2)

    def test_document_reading_units_and_top(self):
        mode = companion.handle('create a document', 'awake')
        companion.handle('Alpha beta. Another sentence.', mode)
        companion.handle('new paragraph', mode)
        companion.handle('Third line.', mode)
        with patch.object(companion, 'speak') as spoken:
            companion.handle('start reading', mode)
            self.assertIn('Alpha beta', spoken.call_args.args[0])
            companion.handle('next paragraph', mode)
            self.assertIn('Third line', spoken.call_args.args[0])
            companion.handle('previous word', mode)
            self.assertIn('Word', spoken.call_args.args[0])
            companion.handle('next character', mode)
            self.assertIn('Character', spoken.call_args.args[0])

    def test_note_reading_uses_same_commands(self):
        mode = companion.handle('write a note', 'awake')
        companion.handle('Remember the meeting.', mode)
        with patch.object(companion, 'speak') as spoken:
            companion.handle('start reading', mode)
            self.assertIn('Remember the meeting', spoken.call_args.args[0])
            companion.handle('next word', mode)
            self.assertIn('Word', spoken.call_args.args[0])

    def test_spoken_voice_picker_previews_then_confirms(self):
        from unittest.mock import MagicMock
        voices = MagicMock()
        voices.Count = 2
        candidates = [MagicMock(), MagicMock()]
        candidates[0].GetDescription.return_value = 'First voice'
        candidates[1].GetDescription.return_value = 'Second voice'
        voices.Item.side_effect = lambda i: candidates[i]
        with patch.object(companion, 'TEXT_MODE', False), patch.object(companion, 'voice', create=True) as speech, \
             patch.object(companion, 'save_speech_settings') as saved:
            speech.Voice = 'Original voice'
            speech.GetVoices.return_value = voices
            self.assertEqual(companion.handle('list voices','awake'), 'awake')
            companion.handle('okay','awake')
            self.assertIs(speech.Voice, candidates[0])
            companion.handle('next voice','awake')
            self.assertIs(speech.Voice, candidates[1])
            saved.assert_not_called()
            companion.handle('confirm that','awake')
            saved.assert_called_once()
            self.assertIs(speech.Voice, candidates[1])
            companion.handle('list voices','awake')
            companion.handle('okay','awake')
            companion.handle('okay','awake')
            self.assertEqual(saved.call_count, 2)

    def test_app_wide_input_modes_and_automatic_document_speech(self):
        mode = companion.handle('create a document', 'awake')
        companion.handle('This is the first sentence.', mode)
        self.assertIn('first sentence', companion.document.paragraphs[0].text)
        companion.handle('commands only', mode)
        companion.handle('Do not add this sentence.', mode)
        self.assertNotIn('Do not add', companion.document.paragraphs[0].text)
        companion.handle('dictation mode', mode)
        companion.handle('read document', mode)
        self.assertIn('read document', companion.document.paragraphs[0].text)
        companion.handle('normal mode', mode)
        companion.handle('Read document', mode)
        self.assertNotIn('Read document', companion.document.paragraphs[0].text)
        self.assertEqual(companion.INPUT_MODE, 'mixed')

    def test_dictation_only_cannot_send_email(self):
        mode = companion.handle('write an email', 'awake')
        with patch.object(companion, 'submit') as sent:
            companion.handle('dictation only', mode)
            companion.handle('send it', mode)
            self.assertIn('send it', companion.email_draft.paragraphs[0].text)
            sent.assert_not_called()
            companion.handle('commands mode', mode)
            self.assertEqual(companion.handle('go to sleep', mode), 'sleep')

    def test_spoken_account_setup_and_switch(self):
        import sys
        from mail_accounts import ProtectedStore
        fake_dpapi = SimpleNamespace(
            CryptProtectData=lambda data, *args: b'protected:' + data,
            CryptUnprotectData=lambda data, *args: ('Voice Companion', data[len(b'protected:'):]))
        with patch.dict(sys.modules, {'win32crypt': fake_dpapi}), patch.object(companion, 'speak') as speech:
            self.assertEqual(companion.handle('email', 'awake'), 'awake')
            self.assertIn('add account', speech.call_args.args[0])
            self.assertEqual(companion.handle('add account', 'awake'), 'awake')
            self.assertIn('Which email account type', speech.call_args.args[0])
            with patch.object(companion, 'connect', return_value='one@example.com') as connect:
                self.assertEqual(companion.handle('Gmail', 'awake'), 'awake')
                connect.assert_called_once_with('gmail', self.root, graphical=True)
            store = ProtectedStore(self.root)
            store.save('gmail', {'address':'one@example.com'})
            store.save('outlook', {'address':'two@example.com'})
            self.assertEqual(companion.handle('switch to one at example dot com', 'awake'), 'awake')
            self.assertEqual(store.selected(), ('gmail','one@example.com'))
            self.assertEqual(companion.handle('write an email', 'awake'), 'email_draft')
            self.assertEqual(companion.email_draft.provider, 'gmail')
            self.assertEqual(companion.handle('switch to Outlook', 'email_draft'), 'email_draft')
            self.assertEqual(companion.email_draft.provider, 'outlook')

    def test_connect_my_gmail_starts_account_authorization(self):
        with patch.object(companion, 'speak') as speech, patch.object(
                companion, 'connect', return_value='david@example.com') as connect:
            self.assertEqual(companion.handle('Connect my Gmail', 'awake'), 'awake')
            connect.assert_called_once_with('gmail', self.root, graphical=True)
            self.assertIn('connected and selected', speech.call_args.args[0])

    def test_build_version_is_reported_by_diagnostics(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['companion.py', '--version']), contextlib.redirect_stdout(output):
            self.assertEqual(companion.main(), 0)
        self.assertEqual(output.getvalue().strip(), 'Voice Companion 0.2.94-test')

    def test_packaged_model_check_never_requires_speech_output(self):
        model = self.root / 'model'
        model.mkdir()
        output = io.StringIO()
        with patch.object(sys, 'argv', ['companion.py', '--check-model']), \
             patch.object(companion, 'TEXT_MODE', False), \
             patch.object(companion, 'MODEL', model), \
             patch.object(companion, 'PARAKEET_MODEL', self.root / 'no-parakeet'), \
             patch.object(companion, 'SetLogLevel'), \
             patch.object(companion, 'Model'), \
             patch.object(companion, 'speak', side_effect=AssertionError('model check must not speak')), \
             contextlib.redirect_stdout(output):
            self.assertEqual(companion.main(), 0)
        self.assertIn('Offline Vosk model loaded successfully', output.getvalue())
        parakeet = self.root / 'parakeet'
        parakeet.mkdir()
        output = io.StringIO()
        with patch.object(sys, 'argv', ['companion.py', '--check-model']), \
             patch.object(companion, 'TEXT_MODE', False), \
             patch.object(companion, 'MODEL', model), \
             patch.object(companion, 'PARAKEET_MODEL', parakeet), \
             patch.object(companion, 'SetLogLevel'), \
             patch.object(companion, 'Model'), \
             patch.object(companion, 'ParakeetRecognition') as recognition, \
             patch.object(companion, 'speak', side_effect=AssertionError('model check must not speak')), \
             contextlib.redirect_stdout(output):
            self.assertEqual(companion.main(), 0)
            recognition.return_value.recognize.assert_called_once()
        self.assertIn('Parakeet model loaded and inference completed', output.getvalue())
        with patch.object(sys, 'argv', ['companion.py', '--check-model']), \
             patch.object(companion, 'TEXT_MODE', False), \
             patch.object(companion, 'MODEL', self.root / 'missing-model'), \
             patch.object(companion, 'speak', side_effect=AssertionError('missing model check must not speak')), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(companion.main(), 2)
        companion.note_buffer = self.old_notes

    def test_damaged_documents_do_not_end_session_or_change_saved_file(self):
        folder = self.root / 'Documents'
        folder.mkdir()
        broken = folder / 'Broken.docx'
        for contents in (b'not a Word file', self.malformed_word_file()):
            broken.write_bytes(contents)
            with patch.object(companion, 'speak') as spoken:
                self.assertEqual(companion.handle('open document Broken', 'awake'), 'awake')
            self.assertIn('could not safely open', spoken.call_args.args[0])
            self.assertEqual(broken.read_bytes(), contents)
            self.assertIsNone(companion.document)

    @staticmethod
    def malformed_word_file():
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('word/document.xml', '<document><broken>')
        return output.getvalue()

    def test_document_survives_restart_and_edit(self):
        mode = companion.handle('create a document', 'awake')
        self.assertEqual(mode, 'document')
        companion.handle('name document Letter', mode)
        companion.handle('start dictation', mode)
        companion.handle('Dear friend. See you soon.', mode)
        self.assertEqual(companion.handle('The instructions say go to sleep tomorrow.', mode), 'document')
        self.assertEqual(companion.handle('The phrase shut down companion is in this letter.', mode), 'document')
        companion.handle('pause dictation', mode)
        companion.document = None
        mode = companion.handle('open document Letter', 'awake')
        self.assertEqual(mode, 'document')
        self.assertIn('Dear friend.', companion.document.process('read document'))
        companion.document.process('next sentence')
        companion.document.process('replace sentence with Goodbye.')
        self.assertIn('Goodbye.', VoiceDocument.open_existing(self.root / 'Documents', 'Letter').process('read document'))

    def test_email_is_saved_and_never_sent(self):
        mode = companion.handle('write an email', 'awake')
        companion.handle('email to friend at example dot com', mode)
        companion.handle('subject is Hello', mode)
        companion.handle('start dictation', mode)
        companion.handle('This is a draft.', mode)
        companion.handle('pause dictation', mode)
        with patch.object(companion.webbrowser, 'open') as open_browser:
            companion.handle('send email', mode)
            open_browser.assert_not_called()
        saved = VoiceEmail.open_existing(self.root / 'Email Drafts', 'Untitled')
        self.assertEqual(saved.recipient, 'friend@example.com')
        self.assertEqual(saved.subject, 'Hello')
        self.assertEqual(saved.paragraphs[0].text, 'This is a draft.')

    def test_direct_task_switch_saves_writing(self):
        mode = companion.handle('create a document', 'awake')
        companion.handle('start dictation', mode)
        companion.handle('Remember this sentence.', mode)
        mode = companion.handle('Write an email.', mode)
        self.assertEqual(mode, 'email_draft')
        saved = VoiceDocument.open_existing(self.root / 'Documents', 'Untitled')
        self.assertIn('Remember this sentence.', saved.process('read document'))
        companion.handle('start dictation', mode)
        companion.handle('Email body.', mode)
        mode = companion.handle('Write a note.', mode)
        self.assertEqual(mode, 'note')
        draft = VoiceEmail.open_existing(self.root / 'Email Drafts', 'Untitled')
        self.assertIn('Email body.', draft.process('read document'))
        companion.handle('Another reminder.', mode)
        mode = companion.handle('Create a document.', mode)
        self.assertEqual(mode, 'document')
        self.assertIn('Another reminder.', (self.root / 'notes.txt').read_text())

    def test_go_back_works_in_note_search_and_podcast(self):
        mode = companion.handle('write a note', 'awake')
        companion.handle('Remember this.', mode)
        self.assertEqual(companion.handle('go back', mode), 'awake')
        self.assertIn('Remember this.', (self.root / 'notes.txt').read_text())
        for mode in ('search', 'podcast'):
            self.assertEqual(companion.handle('go back', mode), 'awake')

    def test_go_back_saves_document_and_allows_email(self):
        mode = companion.handle('Create a document.', 'awake')
        self.assertEqual(mode, 'document')
        mode = companion.handle('Start dictation.', mode)
        mode = companion.handle('A saved sentence.', mode)
        self.assertEqual(companion.handle('Go back.', mode), 'document_save')
        self.assertEqual(companion.handle('yes', 'document_save'), 'document_name')
        self.assertEqual(companion.handle('Keyboard report', 'document_name', typed=True), 'awake')
        self.assertIn('A saved sentence.', VoiceDocument.open_existing(
            self.root / 'Documents', 'Keyboard report').process('read document'))
        mode = companion.handle('Write an email.', 'awake')
        self.assertEqual(mode, 'email_draft')
        self.assertEqual(companion.handle('Exit.', mode), 'awake')
        self.assertTrue((self.root / 'Email Drafts' / 'Untitled.json').exists())

    def test_email_request_after_spoken_repetition(self):
        self.assertEqual(companion.handle('write a write an email', 'awake'), 'email_draft')

    def test_damaged_email_draft_is_preserved_and_does_not_end_session(self):
        folder = self.root / 'Email Drafts'
        folder.mkdir()
        broken = folder / 'Broken.json'
        for contents in ('null', '{"paragraphs": null}',
                         '{"recipient": 4, "paragraphs": []}',
                         '{"paragraphs": [{"text": 7}]}'):
            broken.write_text(contents, encoding='utf-8')
            with patch.object(companion, 'speak') as spoken:
                self.assertEqual(companion.handle('open email draft Broken', 'awake'), 'awake')
            self.assertIn('could not open', spoken.call_args.args[0])
            self.assertEqual(broken.read_text(encoding='utf-8'), contents)
            self.assertIsNone(companion.email_draft)

    def test_dictated_file_commands_stay_in_current_document_and_email(self):
        mode = companion.handle('create a document', 'awake')
        companion.handle('start dictation', mode)
        self.assertEqual(companion.handle('Open document Another Letter', mode), mode)
        self.assertIn('Open document Another Letter', companion.document.paragraphs[0].text)
        self.assertEqual(companion.document.title, 'Untitled')
        self.assertFalse((self.root / 'Documents' / 'Another Letter.docx').exists())
        companion.handle('leave document', mode)
        mode = companion.handle('write an email', 'awake')
        companion.handle('start dictation', mode)
        self.assertEqual(companion.handle('Open document Another Letter', mode), mode)
        self.assertIn('Open document Another Letter', companion.email_draft.paragraphs[0].text)
        self.assertIsNone(companion.pending_website)

    def test_website_requires_confirmation_and_sleep_cancels(self):
        class FakeWeb:
            opened = []
            snapshot = None
            def __init__(self, folder): pass
            def open(self, url):
                self.opened.append(url)
                self.snapshot = {'title': 'Example'}
                return 'Page: Example.'
        with patch.object(companion, 'WebSession', FakeWeb):
            companion.handle('open website example dot com', 'awake')
            self.assertEqual(FakeWeb.opened, [])
            companion.handle('go to sleep', 'awake')
            self.assertIsNone(companion.pending_website)
            companion.handle('yes open website', 'awake')
            self.assertEqual(FakeWeb.opened, [])
            companion.handle('open website example dot com', 'awake')
            self.assertEqual(companion.handle('yes open website', 'awake'), 'web')
            self.assertEqual(FakeWeb.opened, ['https://example.com'])

    def test_no_ready_announcement_if_microphone_cannot_open(self):
        companion.TEXT_MODE = False
        fake_audio = SimpleNamespace(
            query_devices=lambda **kwargs: {'name': 'test microphone', 'default_samplerate': 48000},
            check_input_settings=lambda **kwargs: None,
            RawInputStream=lambda **kwargs: (_ for _ in ()).throw(OSError('no microphone')))
        with (patch.object(companion, 'MODEL', self.root),
              patch.object(companion, 'PARAKEET_MODEL', self.root / 'missing'),
              patch.object(companion, 'Model', lambda path: object()),
              patch.object(companion, 'KaldiRecognizer', lambda model, rate: object()),
              patch.object(companion, 'SetLogLevel'),
              patch.object(companion, 'speak') as spoken,
              patch.dict(sys.modules, {'sounddevice': fake_audio})):
            with self.assertRaises(OSError):
                companion.main()
            self.assertFalse(any('is ready' in call.args[0] for call in spoken.call_args_list))

    def test_missing_offline_model_is_explained_aloud(self):
        companion.TEXT_MODE = False
        with patch.object(companion, 'MODEL', self.root / 'missing-model'), \
             patch.object(companion, 'speak') as spoken, \
             patch.object(sys, 'argv', ['VoiceCompanion.exe']), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(companion.main(), 2)
        self.assertIn('speech files are missing', spoken.call_args.args[0])

    def test_microphone_audio_converts_to_speech_rate(self):
        from array import array
        for source_rate in (8000, 16000, 44100, 48000, 96000):
            converter = PCM16Resampler(source_rate)
            samples = array('h', (1000 if n % 100 < 50 else -1000 for n in range(source_rate)))
            chunks = [samples[i:i + 1000].tobytes() for i in range(0, len(samples), 1000)]
            converted = b''.join(converter.convert(chunk) for chunk in chunks)
            self.assertLessEqual(abs(len(converted) // 2 - 16000), 2)
            self.assertTrue(any(array('h', converted)))

    def test_stereo_microphone_fallback(self):
        def check_settings(**kwargs):
            if kwargs['channels'] == 1:
                raise OSError('mono unavailable')
        device = SimpleNamespace(
            query_devices=lambda **kwargs: {'name': 'stereo mic', 'default_samplerate': 48000,
                                             'max_input_channels': 2},
            check_input_settings=check_settings)
        _, rate, channels = companion.microphone_settings(device)
        self.assertEqual((rate, channels), (48000, 2))
        from array import array
        self.assertEqual(array('h', stereo_to_mono(array('h', [100, 300, -300, 100]).tobytes())).tolist(),
                         [200, -100])

    def test_microphone_diagnostic_reads_back_recognized_words(self):
        from array import array
        captured = array('h', [1000] * 48000).tobytes()
        class FakeStream:
            def __init__(self, callback, **kwargs):
                self.callback = callback
            def __enter__(self):
                self.callback(captured, 48000, None, None)
                return self
            def __exit__(self, *args):
                return False
        class FakeRecognizer:
            def __init__(self, model, rate):
                self.rate = rate
            def AcceptWaveform(self, audio):
                self.audio = audio
            def FinalResult(self):
                self_test.assertTrue(self.audio)
                return '{"text":"wake up create a document"}'
        self_test = self
        device = SimpleNamespace(
            query_devices=lambda **kwargs: {'name': 'test mic', 'default_samplerate': 48000},
            check_input_settings=lambda **kwargs: None,
            RawInputStream=FakeStream)
        output = io.StringIO()
        with (patch.object(companion, 'MODEL', self.root),
              patch.object(companion, 'PARAKEET_MODEL', self.root / 'missing'),
              patch.object(companion, 'Model', lambda path: object()),
              patch.object(companion, 'KaldiRecognizer', FakeRecognizer),
              patch.object(companion, 'SetLogLevel'),
              patch.object(companion, 'speak') as spoken,
              patch.object(companion.threading, 'Event', lambda: SimpleNamespace(wait=lambda seconds: None)),
              patch.dict(sys.modules, {'sounddevice': device}),
              contextlib.redirect_stdout(output)):
            self.assertEqual(companion.microphone_diagnostic(), 0)
        self.assertIn('wake up create a document', output.getvalue())
        self.assertIn('Basic speech heard:', spoken.call_args.args[0])

    def test_parakeet_microphone_check_rejects_missing_dictation(self):
        class FakeStream:
            def __init__(self, callback, **kwargs):
                self.callback = callback
            def __enter__(self):
                self.callback(b'\x01\x00' * 16000, 16000, None, None)
                return self
            def __exit__(self, *args):
                return False
        class FakeRecognizer:
            def __init__(self, model, rate):
                pass
            def AcceptWaveform(self, audio):
                pass
            def FinalResult(self):
                return '{"text":"wake up create a document"}'
        device = SimpleNamespace(
            query_devices=lambda **kwargs: {'name': 'test mic', 'default_samplerate': 16000},
            check_input_settings=lambda **kwargs: None,
            RawInputStream=FakeStream)
        with (patch.object(companion, 'MODEL', self.root),
              patch.object(companion, 'PARAKEET_MODEL', self.root),
              patch.object(companion, 'Model', lambda path: object()),
              patch.object(companion, 'KaldiRecognizer', FakeRecognizer),
              patch.object(companion, 'ParakeetRecognition') as advanced,
              patch.object(companion, 'SetLogLevel'),
              patch.object(companion, 'speak') as spoken,
              patch.object(companion.threading, 'Event', lambda: SimpleNamespace(wait=lambda seconds: None)),
              patch.dict(sys.modules, {'sounddevice': device})):
            advanced.return_value.recognize.return_value = ''
            self.assertEqual(companion.microphone_diagnostic(), 2)
            self.assertIn('dictation test', spoken.call_args.args[0])
            advanced.return_value.recognize.return_value = 'Wake up. Create a document.'
            self.assertEqual(companion.microphone_diagnostic(), 0)

    def test_speaker_tail_is_not_sent_to_recognizer(self):
        old = (companion.MUTE_UNTIL, companion.resampler, companion.input_channels, companion.SPEAKING)
        try:
            companion.resampler = None
            companion.input_channels = 1
            companion.SPEAKING = False
            while not companion.AUDIO.empty():
                companion.AUDIO.get_nowait()
            companion.MUTE_UNTIL = time.monotonic() + 10
            companion.callback(b'\x01\x00' * 200, 200, None, None)
            self.assertTrue(companion.AUDIO.empty())
            companion.MUTE_UNTIL = 0
            companion.callback(b'\x01\x00' * 200, 200, None, None)
            self.assertEqual(len(companion.AUDIO.get_nowait()), 400)
        finally:
            companion.MUTE_UNTIL, companion.resampler, companion.input_channels, companion.SPEAKING = old

    def test_slow_recognition_does_not_accumulate_unbounded_audio(self):
        old = (companion.AUDIO, companion.AUDIO_OVERFLOW, companion.MUTE_UNTIL,
               companion.resampler, companion.input_channels, companion.SPEAKING)
        try:
            companion.AUDIO = companion.queue.Queue(maxsize=2)
            companion.AUDIO_OVERFLOW = False
            companion.MUTE_UNTIL = 0
            companion.resampler = None
            companion.input_channels = 1
            companion.SPEAKING = False
            for _ in range(3):
                companion.callback(b'\x01\x00' * 100, 100, None, None)
            self.assertEqual(companion.AUDIO.qsize(), 2)
            self.assertTrue(companion.AUDIO_OVERFLOW)
            recognizer = SimpleNamespace(Reset=unittest.mock.Mock())
            utterance = bytearray(b'partial words')
            with patch.object(companion, 'speak') as spoken:
                self.assertTrue(companion.recover_audio_overflow(recognizer, utterance))
            recognizer.Reset.assert_called_once()
            self.assertEqual(utterance, b'')
            self.assertTrue(companion.AUDIO.empty())
            self.assertIn('repeat', spoken.call_args.args[0])
        finally:
            (companion.AUDIO, companion.AUDIO_OVERFLOW, companion.MUTE_UNTIL,
             companion.resampler, companion.input_channels, companion.SPEAKING) = old

    def test_sleep_and_shutdown_save_unfinished_notes(self):
        mode = companion.handle('write a note', 'awake')
        companion.handle('Remember my appointment.', mode)
        self.assertEqual(companion.handle('go to sleep', mode), 'sleep')
        self.assertIn('Remember my appointment.', (self.root / 'notes.txt').read_text())
        mode = companion.handle('write a note', 'awake')
        companion.handle('Call my friend.', mode)
        self.assertEqual(companion.handle('shut down companion', mode), 'exit')
        self.assertIn('Call my friend.', (self.root / 'notes.txt').read_text())

    def test_recovery_copies_keep_previous_saved_text(self):
        doc = VoiceDocument(self.root / 'Documents')
        doc.process('start dictation')
        doc.process('First version.')
        doc.process('Second version.')
        prior = doc.path.with_name(doc.path.name + '.backup1')
        self.assertTrue(prior.exists())
        with zipfile.ZipFile(prior) as archive:
            previous_xml = archive.read('word/document.xml').decode()
        self.assertIn('First version.', previous_xml)
        self.assertNotIn('Second version.', previous_xml)
        draft = VoiceEmail(self.root / 'Email Drafts')
        draft.save()
        draft.process('subject is Original')
        draft.process('subject is Revised')
        previous = draft.state_path.with_name(draft.state_path.name + '.backup1')
        self.assertIn('Original', previous.read_text())
        self.assertNotIn('Revised', previous.read_text())


if __name__ == '__main__':
    unittest.main()





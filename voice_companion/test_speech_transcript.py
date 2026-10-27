"""The opt-in speech transcript (speech_transcript.py).

A helper drives the app by keys and reads exactly what a blind user heard, with the
key that caused it. It is off by default, never writes private values, rotates at
about 2 MB, and can never break speech."""
import queue
import unittest
from pathlib import Path
from unittest import mock

import companion
import speech_transcript
from speech_transcript import Transcript, key_label
from test_core_email_fixes import CompanionTestCase

REAL_SPEAK = companion.speak   # before any test replaces it


def lines(folder):
    path = Path(folder) / speech_transcript.NAME
    return path.read_text(encoding='utf-8').splitlines() if path.exists() else []


class TranscriptFileTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.transcript = Transcript(); self.addCleanup(self.transcript.stop)

    def test_off_by_default_writes_nothing(self):
        self.transcript.said('Hello.')
        self.assertFalse((self.folder / speech_transcript.NAME).exists())

    def test_each_line_has_time_mode_and_text_and_is_flushed_at_once(self):
        self.transcript.start(self.folder)
        self.transcript.mode = 'awake'
        self.transcript.key('Down')
        self.transcript.said('Word processor,\n2 of 15.')
        # Read while the file is still open: line buffering has flushed it.
        written = lines(self.folder)
        self.assertEqual(len(written), 2)
        self.assertRegex(written[0], r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} \[awake\] \[key\] Down$')
        self.assertTrue(written[1].endswith('[awake] Word processor, 2 of 15.'))

    def test_stop_ends_writing(self):
        self.transcript.start(self.folder)
        self.transcript.said('One.')
        self.transcript.stop()
        self.transcript.said('Two.')
        self.assertEqual(len(lines(self.folder)), 1)

    def test_rotates_at_the_limit(self):
        transcript = Transcript(limit=200); self.addCleanup(transcript.stop)
        transcript.start(self.folder)
        for number in range(20):
            transcript.said('Sentence number ' + str(number) + ' is here.')
        self.assertTrue((self.folder / speech_transcript.OLD_NAME).exists())
        self.assertLess((self.folder / speech_transcript.NAME).stat().st_size, 200)
        self.assertIn('Sentence number 19', (self.folder / speech_transcript.NAME).read_text(encoding='utf-8'))

    def test_private_values_are_never_written(self):
        self.transcript.start(self.folder)
        self.transcript.remember_private('hunter22secret')
        self.transcript.said('Your password hunter22secret was saved.')
        self.assertNotIn('hunter22secret', '\n'.join(lines(self.folder)))
        self.assertIn('Your password [hidden] was saved.', lines(self.folder)[-1])
        self.assertEqual(key_label(('account_secret', 'hunter22secret')), 'private entry [hidden]')
        self.assertEqual(key_label(('web_private_value', {'value': 'hunter22secret', 'next': 'Tab'})), 'private entry [hidden]')
        self.assertIsNone(key_label(('text_edit', {'after': 'typed words'})))
        self.assertEqual(key_label(('keyboard', 'Ctrl+F1')), 'Ctrl+F1')

    def test_a_write_error_never_breaks_speech(self):
        blocker = self.folder / 'not a folder'
        blocker.write_text('x', encoding='utf-8')
        self.transcript.start(blocker)        # a file where the folder should be
        self.transcript.said('Still spoken.')  # no exception
        self.assertIsNotNone(self.transcript.problem)


class TranscriptInTheAppTests(CompanionTestCase):
    def setUp(self):
        super().setUp()
        self.transcript = Transcript(); self.addCleanup(self.transcript.stop)
        for target, value in ((speech_transcript, self.transcript), (companion, self.transcript)):
            p = mock.patch.object(target, 'TRANSCRIPT', value, create=True); p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(companion.PREFERENCES, {'speech_transcript': False}); p.start(); self.addCleanup(p.stop)
        for name in ('speak', 'speak_prompt', 'speak_status'):
            p = mock.patch.object(companion, name, self._speak); p.start(); self.addCleanup(p.stop)

    def _speak(self, text, *args, **kwargs):
        self.said.append(str(text))
        REAL_SPEAK(text)

    def test_every_speech_path_is_written(self):
        self.transcript.start(self.folder)
        with mock.patch('builtins.print'):
            REAL_SPEAK('From speak.')
            companion.speak_keyboard_feedback('From key feedback.')
        written = '\n'.join(lines(self.folder))
        self.assertIn('From speak.', written)
        self.assertIn('From key feedback.', written)

    def test_voice_commands_start_and_stop_it_and_the_choice_is_saved(self):
        self.assertEqual(companion.handle('start transcript', 'awake'), 'awake')
        self.assertTrue(companion.PREFERENCES['speech_transcript'])
        self.assertTrue(self.transcript.enabled)
        self.assertIn('Say stop transcript', self.said[-1])
        companion.handle('what version', 'awake')
        self.assertTrue(any('Voice Companion version' in line for line in lines(self.folder)))
        companion.handle('Stop transcript.', 'awake')
        self.assertFalse(companion.PREFERENCES['speech_transcript'])
        self.assertFalse(self.transcript.enabled)

    def test_the_words_are_text_in_a_document(self):
        self.open_document()
        companion.INPUT_MODE = 'dictation'
        companion.handle('Start transcript', 'document')
        self.assertFalse(self.transcript.enabled)

    def test_keys_and_the_speech_they_cause_are_written_in_order(self):
        companion.handle('start transcript', 'awake')
        window = mock.Mock(commands=queue.Queue())
        for command in (('keyboard', 'Down'), ('account_secret', 'pa55word!'), ('keyboard', 'Ctrl+V')):
            window.commands.put(command)
        with mock.patch.object(companion, 'APP_WINDOW', window), \
                mock.patch.object(companion, 'finish_account_secret', side_effect=lambda value, mode: (companion.speak('Saved pa55word!.'), mode)[1]):
            companion.process_keyboard_batch('awake')
        written = '\n'.join(lines(self.folder))
        self.assertNotIn('pa55word!', written)
        self.assertLess(written.index('[key] Down'), written.index('[key] private entry [hidden]'))
        self.assertLess(written.index('[key] private entry [hidden]'), written.index('[key] Ctrl+V'))

    def test_the_option_turns_it_on_and_startup_says_so(self):
        companion.PREFERENCES['speech_transcript'] = True
        companion.apply_transcript()
        self.assertTrue(self.transcript.enabled)
        self.assertEqual(self.transcript.folder, self.folder)
        companion.PREFERENCES['speech_transcript'] = False
        companion.apply_transcript()
        self.assertFalse(self.transcript.enabled)

    def test_options_has_the_setting_off_by_default(self):
        from settings_model import DEFAULTS, fields
        self.assertIs(DEFAULTS['speech_transcript'], False)
        labels = [field.label for field in fields('Startup and exit options', {}) if field.key == 'speech_transcript']
        self.assertEqual(labels, ['Keep a transcript of everything Voice Companion says'])


if __name__ == '__main__':
    unittest.main()

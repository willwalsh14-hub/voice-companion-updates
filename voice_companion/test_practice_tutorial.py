import unittest
from unittest.mock import patch

from practice_tutorial import PracticeTutorial


class PracticeTutorialTests(unittest.TestCase):
    def test_radio_lesson_uses_all_preset_save_phrases_without_writing(self):
        for phrase in ('add preset', 'add favorite', 'save preset', 'save favorite'):
            session = PracticeTutorial()
            session.start()
            session.process('practice radio')
            result, _ = session.process('search WGN')
            self.assertIn('Practice result', result)
            result, _ = session.process(phrase)
            self.assertIn('Practice preset saved', result)
            session.process('list presets')
            session.process('that one')
            session.process('delete preset')
            result, _ = session.process('yes')
            self.assertIn('Practice preset deleted', result)

    def test_email_lesson_confirms_delete_and_refreshes_fictional_inbox(self):
        session = PracticeTutorial()
        session.start()
        session.process('practice email')
        session.process('go to inbox')
        session.process('next message')
        session.process('select')
        self.assertIn('Say yes or no', session.process('delete')[0])
        self.assertIn('Inbox refreshed', session.process('yes')[0])
        self.assertIn('Back at the main menu', session.process('main menu')[0])

    def test_tutorial_routes_without_opening_real_mail_or_media(self):
        import companion
        with patch.object(companion, 'speak') as spoken, \
             patch.object(companion, 'MEDIA_HUB', None), \
             patch.object(companion, 'mail_session', None):
            mode = companion.handle('start tutorial', 'awake')
            self.assertEqual(mode, 'tutorial')
            for phrase in ('practice radio', 'search WGN', 'save favorite', 'list presets', 'that one'):
                mode = companion.handle(phrase, mode)
                self.assertEqual(mode, 'tutorial')
            self.assertIsNone(companion.MEDIA_HUB)
            self.assertIsNone(companion.mail_session)
            self.assertIn('No real audio stream', spoken.call_args.args[0])
            self.assertEqual(companion.handle('main menu', mode), 'awake')
            self.assertIsNone(companion.tutorial_session)


if __name__ == '__main__':
    unittest.main()

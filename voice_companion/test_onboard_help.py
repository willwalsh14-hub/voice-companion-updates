"""Release checks for the spoken copy of the complete user guide."""

import unittest
from pathlib import Path
from unittest.mock import patch

from onboard_help import HelpSession


GUIDE = Path(__file__).parent / 'START HERE - Veteran.txt'


class OnboardHelpTests(unittest.TestCase):
    def test_installer_packages_guide_and_runs_onboard_checks(self):
        builder = (GUIDE.parent / 'build-windows.ps1').read_text(encoding='utf-8')
        self.assertIn("'START HERE - Veteran.txt:.'", builder)
        self.assertIn("'test_onboard_help'", builder)

    def test_every_guide_paragraph_is_available_in_a_topic_or_subtopic(self):
        help_session = HelpSession(GUIDE)
        self.assertGreaterEqual(len(help_session.topics), 10)
        for i, topic in enumerate(help_session.topics):
            help_session.topic_index = i
            subsections = help_session._subtopics()
            if subsections:
                self.assertEqual([text for _, paragraphs in subsections for text in paragraphs],
                                 topic['paragraphs'], topic['name'])
            else:
                self.assertTrue(topic['paragraphs'], topic['name'])

    def test_email_picker_reading_navigation_and_return(self):
        session = HelpSession(GUIDE)
        self.assertIn('Read messages (R), 1 of 6', session.topic('email'))
        self.assertIn('Go to folders', session.process('next'))
        self.assertIn('Go to folders', session.process('that one'))
        self.assertTrue(session.process('next paragraph').strip())
        self.assertIn('Go to folders (G), 2 of 6', session.process('back to subsections'))
        self.assertIn(' of ', session.process('back to topics'))
        self.assertIn('Read messages (R), 1 of 6', session.process('back to email topics'))
        self.assertIn('Back where you were', session.process('close help'))
        self.assertTrue(session.closed)

    def test_main_menu_closes_help_from_an_article(self):
        session = HelpSession(GUIDE)
        session.topic('presets')
        session.process('that one')
        self.assertIn('Back at the main menu', session.process('main menu'))
        self.assertTrue(session.closed)

    def test_main_menu_from_help_saves_underlying_document(self):
        import companion
        from unittest.mock import Mock
        document = Mock(title="Saved report")
        document.pending_spacing = True
        with patch.object(companion, 'speak') as spoken, patch.object(companion, 'GUIDE', GUIDE), \
             patch.object(companion, 'document', document):
            self.assertEqual(companion.handle('help with presets', 'document'), 'help')
            self.assertEqual(companion.handle('that one', 'help'), 'help')
            self.assertEqual(companion.handle('main menu', 'help'), 'document_save')
            document.save.assert_not_called()
            self.assertFalse(document.pending_spacing)
            self.assertIsNone(companion.help_session)
            self.assertTrue(any('Save this document?' in c.args[0] for c in spoken.call_args_list))

    def test_web_help_opens_dedicated_topic_and_form_subsections(self):
        session = HelpSession(GUIDE)
        self.assertIn('Browse The Web', session.topic('web'))
        self.assertIn('Open the web browser menu',session.process('list subsections'))
        for _ in range(6):session.process('next')
        self.assertIn('Fill in and review a form by voice',session.process('that one'))
        self.assertIn('Review form',session.reading.text)
        self.assertIn('Change this field',session.reading.text)
        self.assertIn('Yes',session.reading.text)
        self.assertIn('Fill in and review',session.process('back to subsections'))

    def test_direct_help_topics_also_appear_in_main_help_list(self):
        session = HelpSession(GUIDE)
        available = {topic['name'] for topic in session.topics}
        for phrase, expected in (('commands','Using Voice Commands'),
                                 ('documents','Write A Document'),
                                 ('web','Browse The Web'),
                                 ('email','Read And Organize Email'),
                                 ('radio','Listen To Radio'),
                                 ('podcasts','Listen To Podcasts')):
            self.assertIn(expected, available)
            self.assertIn(expected, session.topic(phrase))
        self.assertIn(' of ', session.process('back to topics'))
        self.assertIn('Using Voice Commands', session.topic('commands'))

    def test_preset_help_is_simple_and_separate_from_station_sources(self):
        session = HelpSession(GUIDE)
        self.assertIn('Saved Radio Stations', session.topic('presets'))
        self.assertIn('Save a preset', session.process('that one'))
        self.assertIn('Save preset', session.process('read from top'))
        self.assertNotIn('station database', session.reading.text.casefold())
        self.assertIn('Play saved stations', session.process('back to subsections') and session.process('next'))
        self.assertIn('That one', session.process('that one'))

    def test_companion_help_preserves_mailbox_and_document_modes(self):
        import companion
        with patch.object(companion, 'speak') as spoken, patch.object(companion, 'GUIDE', GUIDE):
            for mode in ('mailbox', 'document'):
                self.assertEqual(companion.handle('help with email', mode), 'help')
                self.assertIn('Read And Organize Email', spoken.call_args.args[0])
                self.assertEqual(companion.handle('next', 'help'), 'help')
                self.assertEqual(companion.handle('that one', 'help'), 'help')
                self.assertIn('Go to folder', spoken.call_args.args[0])
                self.assertEqual(companion.handle('next word', 'help'), 'help')
                self.assertTrue(spoken.call_args.args[0].strip())
                self.assertEqual(companion.handle('back to email topics', 'help'), 'help')
                self.assertEqual(companion.handle('close help', 'help'), mode)

    def test_help_for_web_opens_its_own_subtopics(self):
        import companion
        with patch.object(companion, 'speak') as spoken, patch.object(companion, 'GUIDE', GUIDE):
            self.assertEqual(companion.handle('help for web', 'awake'), 'help')
            self.assertIn('Browse The Web', spoken.call_args.args[0])
            self.assertEqual(companion.handle('next', 'help'), 'help')
            self.assertIn('Search or open an address', spoken.call_args.args[0])
            self.assertEqual(companion.handle('that one', 'help'), 'help')
            self.assertIn('Choose Search or type address',spoken.call_args.args[0])
            self.assertEqual(companion.handle('close help', 'help'), 'awake')

    def test_help_with_commands_is_spoken_and_browseable(self):
        import companion
        with patch.object(companion, 'speak') as spoken, patch.object(companion, 'GUIDE', GUIDE):
            self.assertEqual(companion.handle('help with commands', 'awake'), 'help')
            self.assertIn('Using Voice Commands', spoken.call_args.args[0])
            self.assertEqual(companion.handle('back to topics', 'help'), 'help')
            self.assertIn(' of ', spoken.call_args.args[0])
            self.assertEqual(companion.handle('help', 'help'), 'help')
            self.assertEqual(companion.handle('close help', 'help'), 'awake')


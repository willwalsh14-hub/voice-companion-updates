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
        self.assertIn('Read messages (R), 1 of 7', session.topic('email'))
        self.assertIn('Go to folders', session.process('next'))
        self.assertIn('Go to folders', session.process('that one'))
        self.assertTrue(session.process('next paragraph').strip())
        self.assertIn('Go to folders (G), 2 of 7', session.process('back to subsections'))
        self.assertIn(' of ', session.process('back to topics'))
        self.assertIn('Read messages (R), 1 of 7', session.process('back to email topics'))
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
        self.assertIn('A preset is a saved station', session.process('read from top'))
        self.assertIn('Find or play', session.process('continue reading'))
        self.assertIn('Save preset', session.process('continue reading'))
        self.assertNotIn('station database', session.reading.text.casefold())
        self.assertIn('Play saved stations', session.process('back to subsections') and session.process('next'))
        self.assertIn('List presets', session.process('that one'))
        self.assertIn('That one', session.process('continue reading'))

    def test_block_reading_never_splits_a_long_paragraph_or_skips_a_list_item(self):
        session=HelpSession(GUIDE)
        paragraphs=['First paragraph.','Long paragraph '+('word '*400)+'ends here.','- First choice','- Last choice']
        first=session._open_article(paragraphs,'Example')
        self.assertIn(paragraphs[0],first)
        self.assertEqual(session.process('continue reading'),paragraphs[1])
        self.assertEqual(session.process('next paragraph'),paragraphs[2])
        self.assertEqual(session.process('continue reading'),paragraphs[3])
        self.assertEqual(session.process('continue reading'),'End of topic.')
        self.assertEqual(session.process('previous paragraph'),paragraphs[2])

    def test_every_manual_block_can_be_read_forward_and_back_without_omissions(self):
        session=HelpSession(GUIDE)
        for i,topic in enumerate(session.topics):
            session.topic_index=i
            for name,paragraphs in session._subtopics():
                session._open_article(paragraphs,name)
                actual=[session.reading.move('paragraph',0)]
                actual.extend(session.process('next paragraph') for _ in paragraphs[1:])
                self.assertEqual(actual,paragraphs,name)
                backwards=[session.process('previous paragraph') for _ in paragraphs[1:]]
                self.assertEqual(backwards,list(reversed(paragraphs[:-1])),name)

    def test_unrecognized_speech_while_reading_does_not_interrupt_or_replace_help(self):
        import companion as app
        session=HelpSession(GUIDE);session.topic('documents');session.process('that one')
        position=(session.reading.position,session.reading.continuation)
        with patch.object(app,'help_session',session),patch.object(app,'speak') as say,patch.object(app,'interrupt_speech') as stop,patch.object(app,'sync_app_context'),patch.object(app,'speech_busy',return_value=True):
            self.assertEqual(app.handle('unrelated background conversation','help'),'help')
            stop.assert_not_called();say.assert_not_called()
            self.assertEqual((session.reading.position,session.reading.continuation),position)

    def test_unrecognized_words_get_a_short_answer_when_help_is_quiet(self):
        # Will, October 2026: silence sounded like Help had stopped working.
        import companion as app
        session=HelpSession(GUIDE)
        with patch.object(app,'help_session',session),patch.object(app,'speak') as say,patch.object(app,'interrupt_speech') as stop,patch.object(app,'sync_app_context'),patch.object(app,'speech_busy',return_value=False):
            self.assertEqual(app.handle('banana bread','help'),'help')
            say.assert_called_once_with('I did not understand that. Say next to hear a topic, or press Escape to leave Help.')
            session.topic('documents');session.process('that one')
            position=(session.reading.position,session.reading.continuation)
            app.handle('banana bread','help')
            self.assertIn('Say next to keep reading, or press Escape to go back.',say.call_args.args[0])
            stop.assert_not_called()
            self.assertEqual((session.reading.position,session.reading.continuation),position)
        # Typed words always get the answer, even while Help reads.
        with patch.object(app,'help_session',session),patch.object(app,'speak') as say,patch.object(app,'sync_app_context'),patch.object(app,'speech_busy',return_value=True):
            app.handle('banana bread','help',typed=True)
            self.assertTrue(say.call_args.args[0].startswith('I did not understand that.'))

    def test_keyboard_arrows_and_voice_share_complete_blocks_and_boundaries(self):
        import companion as app
        session=HelpSession(GUIDE);session._open_article(['First.','Second.','Third.'],'Example')
        with patch.object(app,'help_session',session),patch.object(app,'interrupt_speech'),patch.object(app,'sync_app_context'),patch.object(app,'speak') as say:
            for key,expected in [('Help:Down','Second.'),('Up','First.'),('Home','First.'),('Help:End','Third.'),('Help:Down','End of topic. Third.')]:
                request=app.navigation_key(key,'help');app.handle_keyboard(request,'help')
                say.assert_called_with(expected)
            app.handle('previous paragraph','help');say.assert_called_with('Second.')

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
            self.assertIn('Choose Search or enter address',spoken.call_args.args[0])
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


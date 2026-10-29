"""A new veteran's first minutes: startup, sleep and wake, modes, help and the tutorial.

Each test follows what someone who has never used the app says or presses after the
startup announcement and the guide, and checks every word they hear (sweep, October 29, 2026)."""
import unittest
from unittest.mock import patch

import companion
import startup_speech
from onboard_help import HelpSession
from test_core_email_fixes import CompanionTestCase


class WhatModeTests(CompanionTestCase):
    """"What mode?" and Control+M said "Input mode: mixed.", a word the guide never uses."""

    def test_mode_is_said_with_the_names_the_guide_and_mode_commands_use(self):
        for mode, name in (('mixed', 'Normal mode'), ('commands', 'Commands only'), ('dictation', 'Dictation only')):
            with self.subTest(mode=mode):
                companion.INPUT_MODE = mode
                self.said.clear()
                self.assertEqual(companion.handle('What mode?', 'awake'), 'awake')
                self.assertEqual(self.said, ['Input mode: ' + name + '.'])
                self.assertEqual(companion.navigation_key('Ctrl+M', 'awake'), 'what mode')


class TypedWakeUpTests(CompanionTestCase):
    """Typing "Wake up." in the command box while asleep left the screen the user was on,
    said a general list of things the app can do, and went to the main menu."""

    def test_typed_wake_up_returns_to_the_screen_behind_sleep(self):
        for typed in ('Wake up.', 'wake up', 'Wake Companion'):
            with self.subTest(typed=typed):
                companion.SLEEP_RETURN_MODE = 'word_processor'
                self.said.clear()
                self.assertEqual(companion.handle_keyboard(typed, 'sleep'), 'word_processor')
                self.assertEqual(self.said, ['I am listening. Back to Word processor menu.'])

    def test_typed_wake_up_on_the_main_menu_says_the_main_menu(self):
        companion.SLEEP_RETURN_MODE = 'awake'
        self.assertEqual(companion.handle_keyboard('Wake up.', 'sleep'), 'awake')
        self.assertEqual(self.said, [companion.MAIN_MENU_PROMPT])


class WakeDuringAQuestionTests(CompanionTestCase):
    """Going to sleep while Companion asked "Save this document?" and waking again said
    "I am listening. Back to document save." The question was never asked again."""

    def test_waking_repeats_the_save_question(self):
        document = self.open_document()
        companion.handle('Hello there.', 'document')
        self.assertEqual(companion.handle('main menu', 'document'), 'document_save')
        self.assertEqual(companion.handle('Go to sleep.', 'document_save'), 'sleep')
        self.said.clear()
        self.assertEqual(companion.resume_from_sleep(), 'document_save')
        self.assertEqual(self.said, ['I am listening. Save this document? Yes or no.'])
        self.assertIs(companion.document, document)

    def test_waking_repeats_the_replace_question_and_the_name_box(self):
        with patch.object(companion, 'SAVE_AS_TITLE', 'Letter'):
            companion.SLEEP_RETURN_MODE = 'document_replace'
            companion.resume_from_sleep()
            self.assertEqual(self.said[-1], 'I am listening. Replace Letter.docx? Yes or no.')
        companion.SLEEP_RETURN_MODE = 'document_name'
        companion.resume_from_sleep()
        self.assertEqual(self.said[-1], 'I am listening. Back to naming your document.')


class HelpTopicNotFoundTests(CompanionTestCase):
    """"Help with the main menu" said "User guide. I could not identify one help topic. Say back
    to topics to browse the list." The user was left in Help without hearing where they were."""

    def test_an_unknown_topic_says_so_in_plain_words_and_gives_the_topic_list(self):
        self.assertEqual(companion.handle('Help with zebras.', 'awake'), 'help')
        self.assertEqual(len(self.said), 1)
        self.assertTrue(self.said[0].startswith('User guide. I did not find a help topic for zebras. About this guide (A), 1 of '),
                        self.said[0])
        self.assertEqual(companion.help_session.level, 'topics')
        # Next goes on from the topic list, as the message says.
        self.said.clear()
        companion.handle('next', 'help')
        self.assertTrue(self.said[0].startswith('Getting Started (G), 2 of '), self.said)

    def test_an_unknown_topic_inside_an_article_goes_to_the_topic_list(self):
        companion.handle('Help with email', 'awake')
        companion.handle('that one', 'help')
        self.assertEqual(companion.help_session.level, 'article')
        self.said.clear()
        companion.handle('Help with zebras', 'help')
        self.assertEqual(companion.help_session.level, 'topics')
        self.assertTrue(self.said[0].startswith('I did not find a help topic for zebras. Read And Organize Email (R), '), self.said)

    def test_topics_a_new_user_asks_for_open_the_part_that_answers_them(self):
        session = HelpSession(companion.GUIDE)
        for name, part in (('the main menu', 'Use the main menu'), ('main menu', 'Use the main menu'),
                           ('keys', 'Keys that work everywhere'), ('keyboard shortcuts', 'Keys that work everywhere'),
                           ('the microphone', 'Set up your microphone'), ('microphone', 'Set up your microphone'),
                           ('time', 'Check the time and date'), ('the date', 'Check the time and date'),
                           ('time and date', 'Check the time and date'), ('yes or no', 'Answer Yes or No'),
                           ('lessons', 'Start the audio tutorial'), ('transcript', 'Keep a transcript of what Companion says')):
            with self.subTest(name=name):
                said = session.topic(name)
                self.assertEqual(session.level, 'article', said)
                self.assertTrue(said.startswith(part + '. '), said)
        for name, chapter in (('voices', 'Speech And Voices'), ('the options', 'Change Your Options'),
                              ('exit', 'Sleep, Exit, And Computer Power'), ('wake up', 'Sleep, Exit, And Computer Power')):
            with self.subTest(name=name):
                self.assertTrue(session.topic(name).startswith(chapter + '. '), name)

    def test_writing_keeps_a_sentence_that_names_no_topic(self):
        document = self.open_document()
        self.assertEqual(companion.handle('Help with the dishes tonight', 'document'), 'document')
        self.assertEqual(companion.document_text(document), 'Help with the dishes tonight')


class TutorialWordsTests(CompanionTestCase):
    """"Start the tutorial." and "Tutorial" were not understood on the main menu."""

    def test_natural_ways_to_ask_for_the_tutorial_start_it(self):
        for words in ('Start the tutorial.', 'Tutorial', 'Lessons.', 'Open the tutorial', 'Start lessons'):
            with self.subTest(words=words):
                self.said.clear()
                with patch.object(companion, 'tutorial_session', None):
                    self.assertEqual(companion.handle(words, 'awake'), 'tutorial')
                self.assertTrue(self.said[0].startswith('Audio tutorial.'), self.said)

    def test_a_single_word_in_a_document_is_text(self):
        for words in ('Tutorial.', 'Lessons.'):
            with self.subTest(words=words):
                document = self.open_document()
                self.assertEqual(companion.handle(words, 'document'), 'document')
                self.assertEqual(companion.document_text(document), words)


class LessonPartsTests(unittest.TestCase):
    """Long lessons were cut in the middle of a paragraph. A part began "It has the lesson list..."
    with nothing before it, and another ended on a lone step number: "...each time. 4."."""

    def test_parts_break_between_paragraphs_and_never_leave_a_step_number_alone(self):
        from tutorial_curriculum import split_instruction
        bullets = '\n\n'.join('- Bullet number %d says what to press and what you hear after it.' % n for n in range(20))
        text = bullets + '\n\nPress Alt to open the lessons menu. It has the lesson list.'
        parts = split_instruction(text, limit=1000)
        self.assertGreater(len(parts), 1)
        self.assertTrue(parts[-1].endswith('\n\nPress Alt to open the lessons menu. It has the lesson list.'), parts[-1])
        self.assertTrue(all(part.startswith('- Bullet') for part in parts), parts)
        steps = ' '.join('%d. Press the key for step %d and listen to what Companion says.' % (n, n) for n in range(1, 30))
        for part in split_instruction(steps, limit=300):
            with self.subTest(part=part[-30:]):
                self.assertNotRegex(part, r'[.!?"]\s+\d+\.$')
                self.assertRegex(part, r'^\d+\. Press')
        self.assertEqual(split_instruction('One. Two.'), ['One. Two.'])
        # An introduction that ends with a colon stays with what it introduces.
        intro = 'x' * 960 + '.\n\nTo remove an account:\n\n1. Say "Remove email account".'
        self.assertTrue(split_instruction(intro)[-1].startswith('To remove an account:'), split_instruction(intro))

    def test_no_real_lesson_starts_with_a_dangling_word_or_ends_on_a_step_number(self):
        from tutorial_curriculum import build_courses
        for course in build_courses(companion.GUIDE):
            for lesson in course['lessons']:
                with self.subTest(course=course['name'], lesson=lesson['title']):
                    self.assertNotRegex(lesson['text'], r'[.!?"]\s+\d+\.$')
                    self.assertFalse(lesson['text'].startswith(('It has ', 'Then ', 'Everyone can see it.')), lesson['text'][:60])


class MicrophoneThatCannotStartTests(CompanionTestCase):
    """With no working microphone, F9 twice said "Microphone on. Voice commands are on." They were not."""

    def setUp(self):
        super().setUp()
        for name, value in (('MICROPHONE_OFF', False), ('MICROPHONE_FAILED', True), ('MICROPHONE_ANNOUNCED', False)):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.dict(companion.PREFERENCES); p.start(); self.addCleanup(p.stop)

    def test_f9_on_never_says_voice_commands_work_when_the_microphone_cannot_start(self):
        companion.handle('@microphone', 'awake', typed=True)   # off
        self.said.clear()
        companion.handle('@microphone', 'awake', typed=True)   # on again
        self.assertFalse(companion.MICROPHONE_OFF)
        self.assertEqual(self.said, ['Microphone on. It could not start yet, so use the keys. '
                                     'Voice commands start by themselves when it works.'])
        self.assertNotIn('Voice commands are on', self.said[0])


class GuideSilenceWordsTests(unittest.TestCase):
    """The guide said "Pause" silences Companion. It does not: "Pause" pauses radio or a
    podcast, and on the main menu it was "I did not understand that"."""

    def test_every_word_the_guide_gives_to_silence_speech_silences_it(self):
        import re
        for name in ('START HERE - Veteran.txt', 'START HERE - Veteran.html'):
            text = (companion.GUIDE.parent / name).read_text(encoding='utf-8')
            sentence = re.search(r'By voice, say (.+?)\. Say "Resume"', text)
            self.assertIsNotNone(sentence, name)
            words = re.findall(r'"([^"]+)"', sentence[1])
            self.assertTrue(words, name)
            for word in words:
                with self.subTest(guide=name, word=word):
                    self.assertEqual(companion.speech_control(word), 'pause')


class StartupWordsTests(unittest.TestCase):
    """The startup message said "Say start tutorial" while the app was asleep, so saying it did nothing."""

    def test_the_tutorial_is_offered_after_waking(self):
        text = startup_speech.announcement('1.0', 'on')
        self.assertIn('After you wake it, say start tutorial for spoken lessons that wait for you.', text)
        self.assertNotIn('. Say start tutorial', text)
        self.assertIn('Help is available.', text)


if __name__ == '__main__':
    unittest.main()

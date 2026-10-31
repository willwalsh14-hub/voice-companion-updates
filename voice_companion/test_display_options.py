"""Display options for low vision: text size, colors, fonts, the quick keys and voice
commands, and what the window is told to show on each screen (display_screen.py).

The Tk window itself is Windows-only to test; these tests cover the rules it uses."""
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import companion as app
import display_options as options
import display_screen
from app_window import flow_positions, list_lines, position_text, window_geometry
from field_selection import FieldSelection
from key_state import ALT_STATE
from reading_navigation import ReadingCursor
from settings_model import CATEGORIES, DEFAULTS, SettingsSession, fields

CONTROL, SHIFT = 0x4, 0x1


class SettingsTests(unittest.TestCase):
    def test_defaults_are_large_and_high_contrast(self):
        values = options.settings({})
        self.assertEqual(values['display_colors'], 'yellow on black')
        self.assertEqual(options.percent(values), 200)
        self.assertTrue(values['display_full_screen'])
        for key, value in options.DEFAULTS.items():
            self.assertEqual(DEFAULTS[key], value)

    def test_damaged_saved_values_fall_back_to_defaults(self):
        values = options.settings({'display_colors': 'purple', 'display_text_size': '9000', 'display_highlight': 7,
                                   'display_spacing': None, 'display_font': '', 'display_bold': 'yes'})
        self.assertEqual(values['display_colors'], 'yellow on black')
        self.assertEqual(values['display_text_size'], '200 percent')
        self.assertEqual(values['display_highlight'], 'automatic')
        self.assertEqual(values['display_spacing'], 'one and a half')
        self.assertEqual(values['display_font'], 'Verdana')
        self.assertIs(values['display_bold'], True)
        self.assertEqual(options.settings(None)['display_colors'], 'yellow on black')

    def test_text_sizes_step_and_stop_at_the_ends(self):
        self.assertEqual(options.step_size('200 percent', 1), 250)
        self.assertEqual(options.step_size('200 percent', -1), 175)
        self.assertEqual(options.step_size('400 percent', 1), 400)
        self.assertEqual(options.step_size('100 percent', -1), 100)
        for spoken, wanted in (('300', 300), ('300%', 300), ('300 percent.', 300), ('Two hundred fifty percent', 250),
                               (150, 150), ('one hundred', 100)):
            self.assertEqual(options.size_value(spoken), wanted, spoken)
        for wrong in ('120', 'big', '', True, None, '0'):
            self.assertIsNone(options.size_value(wrong), wrong)

    def test_every_font_size_scales_with_text_size(self):
        values = options.settings({'display_text_size': '300 percent'})
        self.assertEqual(options.scaled(12, values), 36)
        family, size, weight, slant = options.font(values, 'title')
        self.assertEqual((family, weight), ('Verdana', 'bold'))
        self.assertEqual(size, options.BASE_SIZES['title'] * 3)
        self.assertEqual(options.font(values, 'body')[2], 'normal')
        self.assertEqual(options.font(dict(values, display_bold=True), 'body')[2], 'bold')

    def test_missing_font_falls_back_to_verdana_and_only_installed_fonts_are_offered(self):
        installed = ['Arial', 'Verdana', 'Segoe UI', 'Tahoma', 'Atkinson Hyperlegible']
        self.assertEqual(options.font_family({'display_font': 'APHont'}, installed), 'Verdana')
        self.assertEqual(options.font_family({'display_font': 'atkinson hyperlegible'}, installed), 'Atkinson Hyperlegible')
        choices = options.font_choices(installed)
        self.assertIn('Atkinson Hyperlegible', choices)
        self.assertNotIn('APHont', choices)
        self.assertIn('APHont', options.font_choices(installed, saved='APHont'))
        self.assertIn('APHont', options.font_choices(None))

    def test_line_spacing_adds_space(self):
        self.assertEqual(sum(options.line_gap({'display_spacing': 'double'}, 40)), 40)
        self.assertEqual(sum(options.line_gap({'display_spacing': 'one and a half'}, 40)), 20)
        self.assertLess(sum(options.line_gap({'display_spacing': 'single'}, 40)), 10)


class ColorTests(unittest.TestCase):
    def test_every_scheme_and_highlight_is_readable(self):
        for scheme in options.SCHEMES:
            for highlight in options.HIGHLIGHTS:
                with self.subTest(scheme=scheme, highlight=highlight):
                    colors = options.colors({'display_colors': scheme, 'display_highlight': highlight})
                    if scheme == 'Windows colors':
                        self.assertEqual(colors['fg'], 'SystemWindowText')
                        continue
                    self.assertGreaterEqual(options.contrast(colors['fg'], colors['bg']), 7)
                    self.assertGreaterEqual(options.contrast(colors['select_fg'], colors['select_bg']), 4.5)
                    # The selected item stands out from the rest of the list.
                    self.assertGreaterEqual(options.contrast(colors['select_bg'], colors['bg']), 3)
                    self.assertGreaterEqual(options.contrast(colors['caret'], colors['bg']), 3)
                    if colors['line']:
                        self.assertGreaterEqual(options.contrast(colors['fg'], colors['line']), 4.5)

    def test_highlight_like_the_background_uses_the_reversed_colors(self):
        colors = options.colors({'display_colors': 'black on yellow', 'display_highlight': 'yellow'})
        self.assertEqual((colors['select_fg'], colors['select_bg']), ('#FFFF00', '#000000'))
        colors = options.colors({'display_colors': 'yellow on black', 'display_highlight': 'cyan'})
        self.assertEqual((colors['select_fg'], colors['select_bg']), ('#000000', '#00FFFF'))

    def test_windows_colors_follow_windows(self):
        colors = options.colors({'display_colors': 'Windows colors'})
        self.assertEqual((colors['bg'], colors['select_bg'], colors['select_fg']),
                         ('SystemWindow', 'SystemHighlight', 'SystemHighlightText'))


class RequestTests(unittest.TestCase):
    def test_spoken_commands_with_recognizer_punctuation(self):
        for said, wanted in (('Bigger text.', ('size', 1)), ('Larger text, please.', ('size', 1)), ('zoom in', ('size', 1)),
                             ('Smaller text!', ('size', -1)), ('Normal text size.', ('size_reset', None)),
                             ('High contrast.', ('scheme', 'yellow on black')), ('Change colors.', ('colors', None)),
                             ('change colours', ('colors', None)), ('Full screen.', ('full_screen', None)),
                             ('Exit full screen.', ('full_screen', False)), ('Text size 300 percent.', ('size_set', 300)),
                             ('Set text size to 150%', ('size_set', 150)), ('White on black.', ('scheme', 'white on black')),
                             ('Use Windows colours', ('scheme', 'Windows colors')), ('What text size?', ('status', None))):
            self.assertEqual(options.voice_request(said), wanted, said)

    def test_sentences_are_not_display_commands(self):
        for said in ('The text is bigger now', 'Bigger text messages are hard to read',
                     'my screen is full', 'contrast', 'colors', '', 'text size is too small'):
            self.assertIsNone(options.voice_request(said), said)
        # A number that is not a size says the sizes and changes nothing (Will, October 31, 2026).
        self.assertEqual(options.voice_request('text size 120'), ('size_invalid', None))

    def test_zoom_keys(self):
        for keysym, state, wanted in (('equal', CONTROL, 'Display:Bigger'), ('plus', CONTROL | SHIFT, 'Display:Bigger'),
                                      ('KP_Add', CONTROL, 'Display:Bigger'), ('minus', CONTROL, 'Display:Smaller'),
                                      ('KP_Subtract', CONTROL, 'Display:Smaller'), ('0', CONTROL, 'Display:Reset'),
                                      ('9', CONTROL, 'Display:Colors'), ('F11', 0, 'Display:FullScreen')):
            self.assertEqual(options.display_key(keysym, state), wanted, keysym)
        # Not the zoom keys: plain typing, Shift with a digit (File manager sorting), AltGr (Control+Alt).
        for keysym, state in (('equal', 0), ('minus', 0), ('0', 0), ('0', CONTROL | SHIFT), ('minus', CONTROL | SHIFT),
                              ('equal', CONTROL | ALT_STATE), ('9', CONTROL | ALT_STATE), ('F11', CONTROL), ('1', CONTROL)):
            self.assertIsNone(options.display_key(keysym, state), (keysym, state))
        for key in options.KEY_REQUESTS:
            self.assertTrue(key.startswith('Display:'))

    def test_changes_say_what_happened(self):
        values, words = options.apply_request({}, ('size', 1))
        self.assertEqual((values['display_text_size'], words), ('250 percent', 'Text size 250 percent.'))
        values, words = options.apply_request({'display_text_size': '400 percent'}, ('size', 1))
        self.assertEqual(words, 'Largest text size. Text size 400 percent.')
        values, words = options.apply_request({'display_text_size': '100 percent'}, ('size', -1))
        self.assertEqual(words, 'Smallest text size. Text size 100 percent.')
        self.assertEqual(options.apply_request({'display_text_size': '400 percent'}, ('size_reset', None))[1], 'Text size 200 percent.')
        values, words = options.apply_request({}, ('colors', None))
        self.assertEqual((values['display_colors'], words), ('white on black', 'White on black.'))
        values, words = options.apply_request({'display_colors': 'Windows colors'}, ('colors', None))
        self.assertEqual(words, 'Yellow on black.')
        self.assertEqual(options.apply_request({}, ('full_screen', None))[1], 'Full screen off.')
        self.assertEqual(options.apply_request({'display_full_screen': False}, ('full_screen', None))[1], 'Full screen on.')
        original = {'display_text_size': '200 percent'}
        options.apply_request(original, ('size', 1))
        self.assertEqual(original, {'display_text_size': '200 percent'})


class OptionsCategoryTests(unittest.TestCase):
    def test_display_options_category(self):
        self.assertIn('Display options', CATEGORIES)
        self.assertEqual(CATEGORIES.index('Display options'), 3)
        keys = [f.key for f in fields('Display options', {})]
        self.assertEqual(keys, ['display_colors', 'display_text_size', 'display_font', 'display_bold',
                                'display_highlight', 'display_spacing', 'display_full_screen'])

    def test_spoken_and_typed_values_are_accepted(self):
        session = SettingsSession({}, {'display_fonts': ['Arial', 'Verdana', 'Tahoma', 'Segoe UI']})
        self.assertEqual(session.set('display_text_size', '300'), 'Text size 300 percent')
        self.assertEqual(session.set('display_text_size', '250%'), 'Text size 250 percent')
        self.assertEqual(session.set('display_colors', 'Black on white'), 'Color scheme black on white')
        self.assertEqual(session.set('display_colors', 'windows colours'), 'Color scheme Windows colors')
        self.assertEqual(session.set('display_font', 'tahoma'), 'Screen font Tahoma')
        with self.assertRaises(ValueError):
            session.set('display_font', 'APHont')
        with self.assertRaises(ValueError):
            session.set('display_text_size', '120')
        self.assertEqual(session.voice_setting('Text size 350.'), ('display_text_size', '350'))
        self.assertEqual(session.voice_setting('highlight color cyan'), ('display_highlight', 'cyan'))

    def test_a_saved_font_that_is_not_installed_stays_in_the_list(self):
        session = SettingsSession({'display_font': 'APHont'}, {'display_fonts': ['Verdana']})
        choices = next(f.choices for f in fields('Display options', session.context) if f.key == 'display_font')
        self.assertIn('APHont', choices)
        self.assertEqual(session.set('display_font', 'APHont'), 'Screen font APHont')


class WindowHelperTests(unittest.TestCase):
    def test_list_lines_mark_the_selected_item_with_an_arrow_not_only_color(self):
        self.assertEqual(list_lines(['Email', 'Contacts'], 1), ['\tEmail', '►\tContacts'])
        self.assertEqual(list_lines(['Email'], None), ['\tEmail'])

    def test_position_text(self):
        self.assertEqual(position_text({'display_index': 2, 'display_count': 15}), '3 of 15')
        self.assertEqual(position_text({'display_index': 0, 'display_count': 900, 'display_offset': 400}), '401 of 900')
        self.assertEqual(position_text({'display_index': None, 'display_count': 3}), '')
        self.assertEqual(position_text({}), '')

    def test_buttons_wrap_instead_of_being_cut_off(self):
        places, height = flow_positions([300, 300, 300], 40, 700)
        self.assertEqual(places, [(0, 0), (306, 0), (0, 46)])
        self.assertEqual(height, 86)
        self.assertEqual(flow_positions([], 40, 700), ([], 0))
        # A button wider than the window still gets a place of its own.
        self.assertEqual(flow_positions([900, 100], 40, 700)[0], [(0, 0), (0, 46)])

    def test_large_window_when_not_full_screen(self):
        self.assertEqual(window_geometry(1920, 1080), '1536x864+192+72')
        self.assertTrue(window_geometry(400, 200).startswith('470x300'))

    def test_long_lists_are_trimmed_around_the_selected_item(self):
        items = [str(i) for i in range(1000)]
        shown, index, offset = options.visible_window(items, 990, 200)
        self.assertEqual((len(shown), shown[index], offset), (200, '990', 800))
        shown, index, offset = options.visible_window(items, None, 200)
        self.assertEqual((len(shown), index, offset), (200, None, 0))


class GuideTests(unittest.TestCase):
    def test_help_topic_and_guide_explain_the_keys_and_commands(self):
        import re
        from onboard_help import HelpSession
        guide = Path(app.__file__).with_name('START HERE - Veteran.txt')
        for said in ('low vision', 'display', 'text size', 'high contrast', 'magnifier', 'large text and colors'):
            self.assertTrue(HelpSession(guide).topic(said).startswith('Large Text And Colors.'), said)
        text = guide.read_text(encoding='utf-8')
        page = re.sub(r'</?kbd>', '', guide.with_suffix('.html').read_text(encoding='utf-8')).replace('&quot;', '"')
        for words in ('Control+Plus', 'Control+Minus', 'Control+0', 'Control+9', 'F11', '"Bigger text"', '"Smaller text"',
                      '"Normal text size"', '"High contrast"', '"Change colors"', '"Full screen"', '25.10 Display options',
                      '30.4 Use full screen and a magnifier'):
            self.assertIn(words, text)
            self.assertIn(words, page)
        # Every voice command the guide gives really works.
        for said in ('Bigger text', 'Smaller text', 'Normal text size', 'High contrast', 'Change colors', 'Full screen',
                     'Text size 300 percent', 'Black on white', 'What text size?'):
            self.assertIsNotNone(options.voice_request(said), said)


class FakeMail:
    def __init__(self, *args): pass
    def folders(self): return [('Inbox', 'inbox')]
    def list_messages(self, folder, cursor=None, limit=10):
        return ([{'id': 'm1', 'from': 'Sam <sam@example.com>', 'subject': 'Lunch', 'date': 'October 26'},
                 {'id': 'm2', 'from': 'Ann <ann@example.com>', 'subject': '', 'date': 'October 25'}], None)
    def folder_info(self, ident): return {'messagesTotal': 2}


class ScreenTests(unittest.TestCase):
    """What sync_app_context tells the window to show, screen by screen."""
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.window = Mock(); self.window.closed.is_set.return_value = False
        defaults = {'APP': self.root, 'TEXT_MODE': True, 'APP_WINDOW': self.window, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': None, 'pending_website': None,
                    'pending_send': None, 'NAMED_LIST': None, 'account_setup': False, 'MAIN_MENU_INDEX': None,
                    'SYNTH_PICK_INDEX': None, 'VOICE_PICK_INDEX': None, 'help_session': None, 'tutorial_session': None,
                    'MEDIA_HUB': None, 'note_buffer': [], 'note_selection': FieldSelection(), 'note_reading': ReadingCursor(),
                    'KEYBOARD_DIRTY': {}, 'EDIT_ACK': {}, 'PREFERENCES': {}, 'SLEEP_RETURN_MODE': 'awake',
                    'FILE_READER': None, 'FILE_MANAGER': None, 'TASK_NAV': None, 'SCREEN_MENU': None, 'SETTINGS_PANEL': None,
                    'ACCOUNT_ENTRY': None, 'ACCOUNT_PICKER': None, 'THUNDERBIRD_SETUP': None, 'CONFIRM_CHOICE': 'yes',
                    'UI_CONTEXT_CACHE': None, 'KEYBOARD_SPEECH': None, 'KEYBOARD_BATCH': False}
        for name, value in defaults.items():
            self.stack.enter_context(patch.object(app, name, value, create=True))
        self.said = []
        for name in ('speak', 'speak_prompt'):
            self.stack.enter_context(patch.object(app, name, side_effect=lambda text, *a, **k: self.said.append(str(text))))
        self.stack.enter_context(patch.object(app, 'interrupt_speech'))

    def shown(self, mode):
        app.UI_CONTEXT_CACHE = None
        app.sync_app_context(mode, True)
        return self.window.set_context.call_args[0][0]

    def test_main_menu_shows_every_item_and_the_selected_one(self):
        context = self.shown('awake')
        self.assertEqual(context['display_title'], 'Main menu')
        self.assertEqual(context['display_items'][:3], ['File manager', 'Word processor', 'Document reader'])
        self.assertIsNone(context['display_index'])
        app.MAIN_MENU_INDEX = 4
        context = self.shown('awake')
        self.assertEqual((context['display_index'], context['display_count']), (4, len(app.MAIN_MENU_CHOICES)))
        self.assertEqual(context['display']['display_colors'], 'yellow on black')

    def test_moving_in_a_list_sends_the_new_selection_to_the_window(self):
        app.MAIN_MENU_INDEX = 0
        self.shown('awake')
        app.MAIN_MENU_INDEX = 1
        self.assertTrue(app.sync_app_context('awake'))
        self.assertEqual(self.window.set_context.call_args[0][0]['display_index'], 1)

    def test_asleep_says_so_in_the_title(self):
        self.assertEqual(self.shown('sleep')['display_title'], 'Main menu, microphone asleep')

    def test_task_menu_lines_show_their_keys(self):
        app.task_navigation().enter('word_processor', 'Word processor menu')
        context = self.shown('word_processor')
        self.assertEqual(context['display_title'], 'Word processor menu')
        self.assertEqual(context['display_items'][0], 'Create a document (Ctrl+N)')
        self.assertEqual(context['display_index'], 0)

    def test_alt_menu_shows_its_groups_then_items_with_keys(self):
        from mail_voice import MailSession
        with patch('mail_voice.account_token', return_value=('me@example.com', 'token')):
            app.mail_session = MailSession('thunderbird', str(self.root), FakeMail)
            app.mail_session.process('open inbox')
        context = self.shown('mailbox')
        self.assertEqual(context['display_title'], 'Email: Inbox')
        self.assertEqual(context['display_items'], ['Sam. Lunch. October 26', 'Ann. No subject. October 25'])
        self.assertEqual(context['display_index'], 0)
        app.screen_menu().open('mailbox')
        context = self.shown('mailbox')
        self.assertEqual(context['display_title'], 'Email menu')
        self.assertEqual(context['display_items'][0], 'Messages')
        app.screen_menu().choose(0, 'mailbox')
        context = self.shown('mailbox')
        self.assertEqual(context['display_title'], 'Email menu: Messages')
        self.assertEqual(context['display_items'][0], 'Check for new mail (F5)')

    def test_yes_or_no_question_shows_the_answers(self):
        context = self.shown('exit_confirm')
        self.assertEqual(context['display_title'], 'Exit Voice Companion')
        self.assertEqual((context['display_items'], context['display_index']), (['Yes', 'No'], 0))
        app.CONFIRM_CHOICE = 'no'
        self.assertEqual(self.shown('exit_confirm')['display_index'], 1)

    def test_note_shows_its_text(self):
        app.note_buffer[:] = ['Buy milk.', 'Call Ann.']
        context = self.shown('note')
        self.assertEqual((context['display_text'], context['display_caret']), ('Buy milk. Call Ann.', 19))
        self.assertNotIn('display_items', context)

    def test_named_list_title(self):
        app.NAMED_LIST = {'title': 'Choose a voice.', 'items': [('Zira', 'z'), ('David', 'd')], 'index': 1}
        context = self.shown('awake')
        self.assertEqual(context['display_title'], 'Choose a voice')
        self.assertEqual((context['display_items'], context['display_index']), (['Zira', 'David'], 1))

    def test_calendar_shows_the_day_and_its_events(self):
        import calendar_screen
        from datetime import date
        event = Mock(); event.line.return_value = 'Ten AM to eleven AM, Doctor, Google'
        with patch.object(calendar_screen, 'DAY', date(2026, 10, 26)), patch.object(calendar_screen, 'EVENTS', [event]), \
                patch.object(calendar_screen, 'INDEX', 0), patch.object(app, 'CLOCK', lambda: __import__('datetime').datetime(2026, 10, 26, 9)):
            context = self.shown('calendar')
        self.assertEqual(context['display_title'], 'Calendar: Today, Monday, October 26')
        self.assertEqual(context['display_items'], ['Ten AM to eleven AM, Doctor, Google'])

    def test_a_display_problem_never_stops_the_window_update(self):
        with patch.object(display_screen, 'context', side_effect=RuntimeError('broken')), redirect_stderr_quiet():
            context = self.shown('awake')
        self.assertEqual(context['mode'], 'awake')
        self.assertEqual(context['display']['display_colors'], 'yellow on black')


class redirect_stderr_quiet:
    def __enter__(self):
        import contextlib
        self.inner = contextlib.redirect_stderr(io.StringIO()); return self.inner.__enter__()
    def __exit__(self, *exc):
        return self.inner.__exit__(*exc)


class CommandTests(ScreenTests):
    """The voice commands and quick keys change, save and announce the display."""
    def saved(self):
        return json.loads((self.root / 'preferences.json').read_text(encoding='utf-8'))

    def test_bigger_text_by_voice_is_saved_said_and_shown(self):
        self.assertEqual(app.handle('Bigger text.', 'awake'), 'awake')
        self.assertEqual(self.said[-1], 'Text size 250 percent.')
        self.assertEqual(app.PREFERENCES['display_text_size'], '250 percent')
        self.assertEqual(self.saved()['display_text_size'], '250 percent')
        self.assertEqual(self.window.set_context.call_args[0][0]['display']['display_text_size'], '250 percent')

    def test_quick_keys(self):
        self.assertIsNone(app.navigation_key('Display:Colors', 'awake'))
        self.assertEqual(self.said[-1], 'White on black.')
        self.assertIsNone(app.navigation_key('Display:Smaller', 'files'))
        self.assertEqual(self.said[-1], 'Text size 175 percent.')
        self.assertIsNone(app.navigation_key('Display:FullScreen', 'document'))
        self.assertEqual(self.said[-1], 'Full screen off.')
        self.assertEqual(self.saved()['display_colors'], 'white on black')

    def test_keys_work_while_an_alt_menu_is_open_and_keep_it_open(self):
        app.MAIN_MENU_INDEX = 0
        app.mail_session = Mock(pending=None, folder_picker=None, folder_choice=None, view='folder', rows=[], selected=set())
        app.screen_menu().open('mailbox')
        app.navigation_key('Display:Bigger', 'mailbox')
        self.assertTrue(app.screen_menu().is_open('mailbox'))

    def test_words_in_a_note_are_text(self):
        app.handle('Bigger text.', 'note')
        self.assertNotIn('Text size 250 percent.', self.said)
        self.assertNotIn('display_text_size', app.PREFERENCES)

    def test_a_failed_save_is_said(self):
        with patch('app_settings.save_preferences', side_effect=OSError('locked')):
            app.handle('high contrast', 'awake')
        self.assertEqual(self.said[-1], 'Yellow on black. This change could not be saved. It lasts until Companion closes.')

    def test_status(self):
        app.handle('What text size?', 'awake')
        self.assertEqual(self.said[-1], 'Text size 200 percent. Yellow on black. Font Verdana. Full screen on.')


if __name__ == '__main__':
    unittest.main()

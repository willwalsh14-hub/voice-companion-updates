"""Checking the time and date by voice and keyboard.

F12, or Insert+F12, says the time. Pressed twice quickly, it says the date.
Control+F12 puts the microphone to sleep and wakes it.
The clock is frozen in every test, so results never depend on the real time.
"""
import io,queue,sys,tempfile,unittest
from contextlib import ExitStack,redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock,patch
import companion as app
import time_date
from field_selection import FieldSelection
from reading_navigation import ReadingCursor

EVENING=datetime(2026,10,22,21,55,30)   # Thursday, October 22, 2026, 9:55 PM


class SpokenWordsTests(unittest.TestCase):
    def test_time_is_spoken_in_twelve_hour_words(self):
        for when,expected in ((EVENING,'9:55 PM'),(datetime(2026,10,22,0,0),'12:00 AM'),
                              (datetime(2026,10,22,12,5),'12:05 PM'),(datetime(2026,10,22,9,5),'9:05 AM'),
                              (datetime(2026,10,22,23,59,59),'11:59 PM')):
            with self.subTest(when=when):self.assertEqual(time_date.spoken_time(when),expected)

    def test_date_says_the_day_month_day_number_and_year(self):
        self.assertEqual(time_date.spoken_date(EVENING,regional=lambda when:None),'Thursday, October 22, 2026')
        self.assertEqual(time_date.spoken_date(datetime(2027,1,1),regional=lambda when:None),'Friday, January 1, 2027')

    def test_windows_regional_date_is_used_and_always_names_the_day(self):
        self.assertEqual(time_date.spoken_date(EVENING,regional=lambda when:'Thursday, 22 October 2026'),'Thursday, 22 October 2026')
        # A long date format without the weekday still tells the user what day it is.
        self.assertEqual(time_date.with_weekday('22 October 2026','Thursday'),'Thursday, 22 October 2026')
        self.assertEqual(time_date.with_weekday('jeudi 22 octobre 2026','jeudi'),'jeudi 22 octobre 2026')
        # Hidden direction marks from some Windows formats are not spoken.
        self.assertEqual(time_date.clean('‎Thursday, ‏October 22, 2026 '),'Thursday, October 22, 2026')
        # A broken or empty Windows answer falls back to plain English.
        for broken in (lambda when:'',lambda when:None,lambda when:1/0):
            self.assertEqual(time_date.spoken_date(EVENING,regional=broken),'Thursday, October 22, 2026')

    @unittest.skipIf(sys.platform=='win32','Windows uses its own regional format')
    def test_regional_format_is_not_used_off_windows(self):
        self.assertIsNone(time_date.regional_long_date(EVENING))


class SpokenRequestTests(unittest.TestCase):
    def test_time_questions(self):
        for phrase in ('time','Time.','what time is it','What time is it?','what’s the time',"What's the time?",
                       'whats the time','what is the time','tell me the time','what time is it now','The time.',
                       'current time','Time please.','Please tell me the time.'):
            with self.subTest(phrase=phrase):self.assertEqual(time_date.clock_request(phrase),'time')

    def test_date_questions(self):
        for phrase in ('date','Date.','what date is it','What date is it?',"today's date","Today's date.",'todays date',
                       "what's the date","What's the date?",'what is the date','the date','tell me the date',
                       "what's today's date",'what is the date today','what day is it','What day is it?',
                       'what day is today','what day is it today'):
            with self.subTest(phrase=phrase):self.assertEqual(time_date.clock_request(phrase),'date')

    def test_both_together(self):
        for phrase in ('date and time','What is the date and time?','time and date',"what's the time and date"):
            with self.subTest(phrase=phrase):self.assertEqual(time_date.clock_request(phrase),'both')

    def test_sentences_are_not_requests(self):
        for phrase in ('Time to go home.','The date of the party is Friday.','What time is the meeting?',
                       'Date night is Friday.','times','dates','update','time zone','what time is it in Tokyo',
                       'save the date','the time has come','Do you have the time to help?',''):
            with self.subTest(phrase=phrase):self.assertIsNone(time_date.clock_request(phrase))

    def test_single_words_are_text_where_people_write(self):
        """In a document, email, note or name box, "Time." or "The date." alone is written, not answered."""
        for phrase in ('time','Time.','the time','date','Date.','The date.','date and time'):
            with self.subTest(phrase=phrase):self.assertIsNone(time_date.clock_request(phrase,writing=True))
        for phrase,expected in (('What time is it?','time'),("What's the time?",'time'),('tell me the time','time'),
                                ("What's the date?",'date'),("Today's date.",'date'),('What day is it?','date'),
                                ('what date is it','date'),('What is the date and time?','both')):
            with self.subTest(phrase=phrase):self.assertEqual(time_date.clock_request(phrase,writing=True),expected)


class KeyTests(unittest.TestCase):
    def test_f12_and_insert_f12_check_the_clock_and_control_f12_does_not(self):
        self.assertTrue(time_date.clock_key('F12',0))
        self.assertTrue(time_date.clock_key('F12',0,insert_held=True))
        # Lock keys and Alt do not matter. On Windows, Tk reports Num Lock as 8,
        # the bit that means Alt on Linux, so F12 must not depend on it.
        for state in (2,8,0x10,0x20000,8|2):
            with self.subTest(state=state):self.assertTrue(time_date.clock_key('F12',state))
        for state in (4,1,4|1,4|8):
            with self.subTest(state=state):self.assertFalse(time_date.clock_key('F12',state))
        for keysym in ('F11','Insert','f','F1'):
            self.assertFalse(time_date.clock_key(keysym,0,insert_held=True))

    def test_second_press_within_half_a_second_is_a_double_press(self):
        moments=iter([10.0,10.4,12.0,12.6,20.0,20.3,20.5])
        counter=time_date.PressCounter(clock=lambda:next(moments))
        self.assertEqual([counter.press() for _ in range(7)],[1,2,1,1,1,2,1])
        self.assertEqual(time_date.DOUBLE_PRESS_SECONDS,0.5)
        self.assertEqual(time_date.key_request(1),'F12')
        self.assertEqual(time_date.key_request(2),'Double:F12')

    def test_web_app_mode_keeps_the_clock_and_sleep_keys(self):
        from web_modes import COMPANION_KEYS,window_request
        self.assertIn('F12',COMPANION_KEYS);self.assertIn('Control+F12',COMPANION_KEYS)
        for state in (0,4):
            self.assertIsNone(window_request('F12','',state,{'web_app_mode':True}))


class ClockScreenTests(unittest.TestCase):
    """The keys and spoken requests on every screen, with the clock frozen."""

    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults={'APP':self.root,'TEXT_MODE':True,'APP_WINDOW':None,'INPUT_MODE':'mixed','document':None,
                  'email_draft':None,'mail_session':None,'web_session':None,'pending_website':None,
                  'pending_send':None,'NAMED_LIST':None,'account_setup':False,'MAIN_MENU_INDEX':None,
                  'help_session':None,'tutorial_session':None,'MEDIA_HUB':None,'note_buffer':[],
                  'note_selection':FieldSelection(),'note_reading':ReadingCursor(),
                  'KEYBOARD_DIRTY':{},'EDIT_ACK':{},'PREFERENCES':dict(app.PREFERENCES),'ACCOUNT_ENTRY':None,
                  'ACCOUNT_PICKER':None,'THUNDERBIRD_SETUP':None,'FILE_MANAGER':None,
                  'SLEEP_RETURN_MODE':'awake','CLOCK':lambda:EVENING}
        for name,value in defaults.items():
            self.stack.enter_context(patch.object(app,name,value,create=name=='CLOCK'))
        self.stack.enter_context(patch.object(time_date,'regional_long_date',lambda when:None))
        self.speech=self.stack.enter_context(patch.object(app,'speak'))
        self.stack.enter_context(patch.object(app,'speak_prompt',side_effect=lambda text,*a,**k:app.speak(text)))
        self.stack.enter_context(patch.object(app,'dictation_feedback',side_effect=lambda text,*a,**k:app.speak(text)))

    def said(self):return [str(call.args[0]) for call in self.speech.call_args_list]

    def open_document(self):
        from document_editor import VoiceDocument,Paragraph
        app.document=VoiceDocument(self.root/'Docs',title='Clock check')
        app.document.paragraphs=[Paragraph('Hello world.')];app.document.dictating=True
        return app.document

    def open_draft(self):
        from email_draft import VoiceEmail,Paragraph
        app.email_draft=VoiceEmail(self.root/'Drafts',title='Clock check',recipient='friend@example.com',
                                   subject='Hi',provider='gmail',compose_step='body')
        app.email_draft.paragraphs=[Paragraph('Hello world.')];app.email_draft.dictating=True
        return app.email_draft

    def test_f12_says_the_time_and_twice_says_the_date_on_every_screen(self):
        for mode in ('awake','document','email_draft','note','mailbox','web','files','file_reader','media','help',
                     'tutorial','word_processor','browser_menu','settings','search','document_name','exit_confirm'):
            for key,expected in (('F12','9:55 PM.'),('Double:F12','Thursday, October 22, 2026.')):
                with self.subTest(mode=mode,key=key):
                    self.speech.reset_mock()
                    self.assertIsNone(app.navigation_key(key,mode))
                    self.assertEqual(self.said(),[expected])

    def test_clock_keys_work_in_lists_menus_questions_and_account_setup(self):
        states={'NAMED_LIST':{'items':[('One','one')],'index':0},'ACCOUNT_ENTRY':{'step':'password'},
                'ACCOUNT_PICKER':{'mode':'awake','items':[]},'CONFIRM_CHOICE':'yes'}
        for name,value in states.items():
            with self.subTest(state=name),patch.object(app,name,value):
                self.speech.reset_mock()
                self.assertIsNone(app.navigation_key('F12','awake'))
                self.assertEqual(self.said(),['9:55 PM.'])
                self.assertEqual(getattr(app,name),value)
        with patch.object(app.screen_menu(),'keyboard',return_value='Escape'):
            self.speech.reset_mock()
            self.assertIsNone(app.navigation_key('Double:F12','document'))
            self.assertEqual(self.said(),['Thursday, October 22, 2026.'])

    def test_keys_work_while_the_microphone_sleeps_and_control_f12_wakes_it(self):
        # The app window's queue of key presses; the rest of the window is a stand-in.
        window=Mock(commands=queue.Queue(),ui_actions=queue.Queue(),feedback=lambda text:app.speak(text))
        with patch.object(app,'APP_WINDOW',window),patch.object(app,'sync_app_context'),patch.object(app,'announce_main_menu'):
            app.SLEEP_RETURN_MODE='document';self.open_document()
            window.commands.put(('keyboard','F12'))
            self.assertEqual(app.process_keyboard_batch('sleep'),'sleep')
            self.assertEqual(self.said()[-1],'9:55 PM.')
            window.commands.put(('keyboard','F12'));window.commands.put(('keyboard','Double:F12'))
            self.assertEqual(app.process_keyboard_batch('sleep'),'sleep')
            self.assertEqual(self.said()[-1],'Thursday, October 22, 2026.')
            self.assertEqual(app.SLEEP_RETURN_MODE,'document')
            window.commands.put(('keyboard','Ctrl+F12'))
            self.assertEqual(app.process_keyboard_batch('sleep'),'document')
            window.commands.put(('keyboard','Ctrl+F12'))
            self.assertEqual(app.process_keyboard_batch('document'),'sleep')
            self.assertEqual(app.SLEEP_RETURN_MODE,'document')
            self.assertEqual([p.text for p in app.document.paragraphs],['Hello world.'])

    def test_control_f12_replaces_f12_for_microphone_sleep(self):
        from input_parity import SCREEN_KEYS
        for mode in ('awake','note','files','document','web','mailbox','help','media','file_reader'):
            with self.subTest(mode=mode):
                self.assertEqual(app.navigation_key('Ctrl+F12',mode),'go to sleep')
                self.assertNotIn('F12',SCREEN_KEYS.get(mode,{}))
        self.assertEqual(SCREEN_KEYS['sleep'],{'Ctrl+F12':'wake up'})

    def test_spoken_questions_on_every_screen_leave_the_screen_alone(self):
        app.MEDIA_HUB=Mock()
        for mode in ('awake','document','email_draft','note','mailbox','web','help','files','media'):
            for phrase,expected in (('What time is it?','9:55 PM.'),("What's the date?",'Thursday, October 22, 2026.'),
                                    ('What day is it?','Thursday, October 22, 2026.'),
                                    ('date and time','9:55 PM. Thursday, October 22, 2026.')):
                with self.subTest(mode=mode,phrase=phrase):
                    if mode=='document':self.open_document()
                    if mode=='email_draft':self.open_draft()
                    if mode=='mailbox':app.mail_session=Mock(folder_picker=None,folder_choice=None,pending=None,view='folder')
                    if mode=='web':app.web_session=Mock(pending=None,submit_pending=None,list_focus=None,keyboard_field=None)
                    if mode=='help':app.help_session=app.HelpSession(app.GUIDE);app.help_return_mode='awake'
                    if mode=='files':
                        from file_manager import FileManager
                        from test_file_manager import FakeFiles
                        app.FILE_MANAGER=FileManager(FakeFiles(self.root))
                    app.note_buffer[:]=[]
                    self.speech.reset_mock()
                    if phrase=='date and time' and mode in ('document','email_draft','note'):continue
                    self.assertEqual(app.handle(phrase,mode),mode)
                    self.assertEqual(self.said(),[expected])
                    if mode=='document':self.assertEqual([p.text for p in app.document.paragraphs],['Hello world.'])
                    if mode=='email_draft':self.assertEqual([p.text for p in app.email_draft.paragraphs],['Hello world.'])
                    if mode=='note':self.assertEqual(app.note_buffer,[])

    def test_writing_keeps_sentences_and_single_words_as_text(self):
        for phrase in ('Time to go home.','The date of the party is Friday.','Time.','Date.','The date.'):
            with self.subTest(phrase=phrase):
                document=self.open_document();self.speech.reset_mock()
                self.assertEqual(app.handle(phrase,'document'),'document')
                self.assertNotIn('9:55 PM.',self.said())
                self.assertNotEqual([p.text for p in document.paragraphs],['Hello world.'])
                self.assertIn(phrase.rstrip('.').split()[-1].rstrip('.').lower(),' '.join(p.text for p in document.paragraphs).lower())
                draft=self.open_draft()
                self.assertEqual(app.handle(phrase,'email_draft'),'email_draft')
                self.assertNotEqual([p.text for p in draft.paragraphs],['Hello world.'])
                app.note_buffer[:]=[]
                self.assertEqual(app.handle(phrase,'note'),'note')
                self.assertTrue(app.note_buffer,phrase)
        # Typed in the note box, a single word is part of the note too.
        app.note_buffer[:]=[];self.speech.reset_mock()
        app.handle_keyboard('Date','note')
        self.assertEqual(app.note_buffer,['Date']);self.assertNotIn('Thursday, October 22, 2026.',self.said())

    def test_single_words_are_commands_off_writing_screens_and_in_commands_only(self):
        for mode in ('awake','mailbox','media'):
            if mode=='mailbox':app.mail_session=Mock(folder_picker=None,folder_choice=None,pending=None,view='folder')
            if mode=='media':app.MEDIA_HUB=Mock()
            for phrase,expected in (('Time.','9:55 PM.'),('date','Thursday, October 22, 2026.')):
                with self.subTest(mode=mode,phrase=phrase):
                    self.speech.reset_mock()
                    self.assertEqual(app.handle(phrase,mode),mode)
                    self.assertEqual(self.said(),[expected])
        document=self.open_document()
        with patch.object(app,'INPUT_MODE','commands'):
            self.speech.reset_mock()
            self.assertEqual(app.handle('Time.','document'),'document')
            self.assertEqual(self.said(),['9:55 PM.'])
        # Alt+T in a document is a typed command.
        self.speech.reset_mock()
        self.assertEqual(app.handle('date','document',typed=True),'document')
        self.assertEqual(self.said(),['Thursday, October 22, 2026.'])
        self.assertEqual([p.text for p in document.paragraphs],['Hello world.'])

    def test_search_and_name_boxes_keep_single_words(self):
        with patch.object(app,'run_web_search',return_value='search') as search:
            app.web_session=Mock()
            app.handle('Date','search')
        search.assert_called_once_with('Date')
        self.assertNotIn('Thursday, October 22, 2026.',self.said())
        self.speech.reset_mock()
        self.assertEqual(app.handle('What time is it?','search'),'search')
        self.assertEqual(self.said(),['9:55 PM.'])


if __name__=='__main__':
    unittest.main()

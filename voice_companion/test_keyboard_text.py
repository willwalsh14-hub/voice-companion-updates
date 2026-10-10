import copy
import json
from pathlib import Path
import queue
import string
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
import companion
from app_window import CompanionWindow
from document_editor import VoiceDocument,Paragraph,TextRun
from keyboard_speech import KeyboardSpeech
from keyboard_text import document_text,replace_keyboard_text,caret_feedback,typing_feedback,phonetic,character
from settings_model import DEFAULTS,CATEGORIES,fields

class KeyboardTextTests(unittest.TestCase):
    def test_document_save_no_restores_existing_file_and_removes_new_file(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder),title='Existing');doc.append_text('Original.');original=doc.path.read_bytes()
            with patch.object(companion,'document',doc),patch.object(companion,'speak'),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'DOCUMENT_BASELINE',None):
                companion.checkpoint_document();doc.append_text('Changed.')
                self.assertNotEqual(doc.path.read_bytes(),original)
                self.assertEqual(companion.handle('main menu','document'),'document_save')
                self.assertEqual(companion.handle('no','document_save'),'awake')
                self.assertEqual(doc.path.read_bytes(),original)
                fresh=VoiceDocument(Path(folder))
                companion.document=fresh;companion.checkpoint_document();fresh.append_text('Discard me.')
                self.assertEqual(companion.handle('leave document','document'),'document_save')
                self.assertEqual(companion.handle('no','document_save'),'awake');self.assertFalse(fresh.path.exists())
    def test_f2_names_in_place_and_save_prompt_supports_keyboard(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.append_text('Body stays.')
            with patch.object(companion,'document',doc),patch.object(companion,'speak'),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'DOCUMENT_NAME_RETURN','awake'),patch.object(companion,'DOCUMENT_BASELINE',None):
                command=companion.navigation_key('F2','document')
                self.assertEqual(companion.handle_keyboard(command,'document'),'document_name')
                self.assertEqual(companion.handle_keyboard('My file','document_name'),'document')
                self.assertEqual(doc.title,'My file');self.assertIn('Body stays.',document_text(doc))
                self.assertEqual(companion.handle('main menu','document'),'document_save')
                self.assertEqual(companion.navigation_key('Down','document_save'),None)
                self.assertEqual(companion.navigation_key('Enter','document_save'),'no')
                self.assertEqual(companion.navigation_key('Escape','document_save'),'cancel')
                self.assertEqual(companion.handle('cancel','document_save'),'document')
    def test_exit_aliases_and_menu_exit(self):
        for command in ('close companion','exit companion','return to windows'):
            self.assertEqual(companion.spoken_control(command),'exit')
        self.assertEqual(companion.MAIN_MENU_CHOICES[-1],('Exit Voice Companion','exit companion'))
    def test_mail_shortcuts_and_pending_keyboard_confirmation(self):
        session=Mock(pending=None,folder_picker=None,folder_choice=None,view='folder')
        with patch.object(companion,'mail_session',session),patch.object(companion,'speak'):
            for key,request in {'Ctrl+Y':'go to folder','Ctrl+R':'reply','Ctrl+Shift+R':'reply all','Ctrl+F':'forward','Ctrl+N':'new email','Delete':'delete','Space':'toggle message selection','Ctrl+A':'select all messages','Ctrl+Shift+V':'move','F2':'rename folder','Ctrl+Shift+D':'delete folder'}.items():
                self.assertEqual(companion.navigation_key(key,'mailbox'),request)
            session.pending=('delete',[],None);companion.CONFIRM_CHOICE='yes'
            companion.navigation_key('Right','mailbox');self.assertEqual(companion.navigation_key('Enter','mailbox'),'no')
    def test_rename_folder_accepts_typed_name(self):
        session=Mock(folder_choice=('rename','Old','id'))
        with patch.object(companion,'mail_session',session),patch.object(companion,'handle',return_value='mailbox') as handle:
            companion.handle_keyboard('New folder','mailbox')
            handle.assert_called_once_with('rename to New folder','mailbox',typed=True)
    def test_pause_resume_and_stop_control_the_keyboard_voice_on_its_owner_thread(self):
        worker=KeyboardSpeech.__new__(KeyboardSpeech);worker.settings=('',0,100,None);worker.problem=None;worker.paused=False;worker.active=True;worker.quiet_until=0
        worker.queue=Mock(get=Mock(side_effect=['Read this.',('pause',),('resume',),('interrupt',),None]))
        voice=Mock()
        with patch.dict(sys.modules,{'pythoncom':Mock(),'win32com':Mock(),'win32com.client':Mock(Dispatch=Mock(return_value=voice))}):worker.run()
        self.assertEqual([call[0] for call in voice.mock_calls],['Speak','Pause','Resume','Speak'])
        self.assertEqual(voice.Speak.call_args.args,('',3));self.assertFalse(worker.active)
        with patch.object(companion,'KEYBOARD_SPEECH',Mock()) as output,patch.object(companion,'TEXT_MODE',True),patch.object(companion,'SPEECH_PAUSED',False):
            companion.control_speech('pause');companion.control_speech('resume');companion.interrupt_speech()
            self.assertEqual([call.args for call in output.control.call_args_list],[('pause',),('resume',)])
            output.interrupt.assert_called_once()
    def test_stop_discards_pending_keyboard_readback(self):
        worker=KeyboardSpeech.__new__(KeyboardSpeech);worker.queue=queue.Queue()
        worker.queue.put('Old paragraph.');worker.queue.put('Old character.')
        worker.interrupt()
        self.assertEqual(worker.queue.get_nowait(),('interrupt',));self.assertTrue(worker.queue.empty())
    def test_fast_espeak_review_discards_obsolete_feedback_before_speaking(self):
        worker=KeyboardSpeech.__new__(KeyboardSpeech)
        engine=Mock(enabled=True);worker.settings=('',2,75,engine);worker.problem=None
        worker.queue=Mock(get=Mock(side_effect=['b','c',None]))
        with patch.dict(sys.modules,{'pythoncom':Mock(),'win32com':Mock(),'win32com.client':Mock(Dispatch=Mock(return_value=Mock()))}):worker.run()
        self.assertEqual([call[0] for call in engine.mock_calls],['interrupt','speak','interrupt','speak'])
        self.assertEqual(engine.speak.call_args.args,('c',2,75))
    def test_espeak_narration_does_not_mark_keyboard_feedback_busy(self):
        worker=KeyboardSpeech.__new__(KeyboardSpeech)
        worker.active=False;worker.quiet_until=0;worker.problem=None
        worker.settings=('',0,100,Mock(enabled=True,busy=Mock(return_value=True)))
        worker.queue=Mock(get=Mock(side_effect=[queue.Empty(),None]))
        sapi=Mock(WaitUntilDone=Mock(return_value=True))
        with patch.dict(sys.modules,{'pythoncom':Mock(),'win32com':Mock(),'win32com.client':Mock(Dispatch=Mock(return_value=sapi))}):
            worker.run()
        self.assertFalse(worker.busy())
    def test_literal_edit_preserves_punctuation_and_surrounding_formatting(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Hello, world.',kind='Heading1',font='Arial',size=32,runs=[TextRun('Hello, ',bold=True),TextRun('world.',italic=True)]),Paragraph('Keep this.',alignment='right')]
            untouched=copy.deepcopy(doc.paragraphs[1])
            replace_keyboard_text(doc,'Hello, world.\nKeep this.','Hello, file.\nKeep this.',11)
            self.assertEqual(document_text(doc),'Hello, file.\nKeep this.')
            self.assertEqual(doc.paragraphs[1],untouched)
            self.assertEqual(doc.paragraphs[0].kind,'Heading1')
            self.assertTrue(doc.paragraphs[0].runs[0].bold)
            self.assertIsNone(doc.selection)
    def test_keyboard_return_creates_a_paragraph_without_dictation_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('new line period')]
            replace_keyboard_text(doc,'new line period','new line\nperiod',9)
            self.assertEqual([p.text for p in doc.paragraphs],['new line','period'])
    def test_stale_edit_cannot_replace_more_recent_voice_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('New text')]
            with self.assertRaises(ValueError):replace_keyboard_text(doc,'Old text','Old texts',9)
            self.assertEqual(document_text(doc),'New text')
    def test_character_word_line_and_selection_feedback(self):
        self.assertEqual(caret_feedback('abc',1,'Right'),('b','b'))
        self.assertEqual(caret_feedback('One two.',4,'Right',True),('two.',''))
        self.assertEqual(caret_feedback('First\nSecond',8,'Down'),('Second',''))
        self.assertEqual(caret_feedback('One two',3,'Right',False,(0,3)),('Selected One',''))
        self.assertEqual(phonetic('B'),'Bravo')
    def test_character_review_names_punctuation_instead_of_sending_silent_symbols_to_espeak(self):
        from app_window import entry_feedback
        for symbol in string.punctuation:
            self.assertNotEqual(character(symbol),symbol)
            self.assertNotEqual(entry_feedback(symbol,symbol,1,0,'Left'),symbol)
        self.assertEqual(character('/'),'forward slash')
        self.assertEqual(character('\u00a0'),'no-break space')
    def test_all_typing_echo_options_and_replacement(self):
        self.assertEqual(typing_feedback('tes','test',4,'characters'),'t')
        self.assertIsNone(typing_feedback('tes','test',4,'words'))
        self.assertEqual(typing_feedback('test','test ',5,'words'),'test')
        self.assertEqual(typing_feedback('test','test ',5,'characters and words'),'space. test')
        self.assertIsNone(typing_feedback('tes','test',4,'none'))
        self.assertEqual(typing_feedback('cat','bat',1,'characters'),'b')
    def test_keyboard_voice_preferences_persist_without_reconfiguring_accounts_or_engines(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(companion,'APP',Path(folder)),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'KEYBOARD_DIRTY',{}),patch.object(companion,'PREFERENCES',dict(DEFAULTS)),patch.object(companion,'speak'),patch.object(companion,'apply_settings',side_effect=AssertionError('Unrelated engines must not be reconfigured')):
            for command,key,value in [('typing echo words','typing_echo','words'),('phonetics off','phonetic_enabled',False),('phonetic delay 1 second','phonetic_delay','1')]:
                self.assertEqual(companion.handle(command,'awake'),'awake')
                self.assertEqual(json.loads((Path(folder)/'preferences.json').read_text())[key],value)
    def test_punctuation_echo_and_phonetics_are_in_verbosity(self):
        self.assertNotIn('Punctuation',CATEGORIES)
        keys={f.key for f in fields('Verbosity',{})}
        self.assertTrue({'punctuation','typing_echo','phonetic_enabled','phonetic_delay'}<=keys)
    def test_typing_and_menu_commands_work_without_waking_microphone(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Hello')]
            with patch.object(companion,'document',doc),patch.object(companion,'SLEEP_RETURN_MODE','document'),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'KEYBOARD_DIRTY',{}),patch.object(companion,'EDIT_ACK',{}):
                self.assertTrue(companion.apply_keyboard_edit(dict(source=str(id(doc)),before='Hello',after='Hello!',caret=6,seq=1),'sleep'))
                self.assertEqual(document_text(doc),'Hello!')
                self.assertEqual(companion.SLEEP_RETURN_MODE,'document')
        with patch.object(companion,'SLEEP_RETURN_MODE','awake'),patch.object(companion,'handle',return_value='awake') as handle,patch.object(companion,'APP_WINDOW',None):
            self.assertEqual(companion.handle_keyboard('next','sleep'),'sleep')
            handle.assert_called_once_with('next','awake',typed=True)
    def test_spoken_document_name_waits_for_enter_or_voice_confirmation(self):
        for confirmation in ('typed enter','okay'):
            with tempfile.TemporaryDirectory() as folder:
                doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Separate body.')]
                with patch.object(companion,'document',doc),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'KEYBOARD_DIRTY',{}),patch.object(companion,'PENDING_DOCUMENT_NAME',None),patch.object(companion,'speak'),patch.object(companion,'INPUT_MODE','mixed'):
                    mode=companion.handle('leave document','document')
                    self.assertEqual(mode,'document_save');mode=companion.handle('yes',mode)
                    self.assertEqual(companion.handle('Spoken report',mode),'document_name')
                    self.assertFalse((Path(folder)/'Spoken report.docx').exists())
                    self.assertEqual(companion.app_context('document_name')['naming_request'][1],'Spoken report')
                    result=companion.handle('Spoken report' if confirmation=='typed enter' else 'okay','document_name',typed=confirmation=='typed enter')
                    self.assertEqual(result,'awake');self.assertTrue((Path(folder)/'Spoken report.docx').exists())
                    self.assertEqual(document_text(doc),'Separate body.')
    def test_untitled_exit_prompts_and_name_is_not_document_text(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Body stays separate.')]
            with patch.object(companion,'document',doc),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'KEYBOARD_DIRTY',{}),patch.object(companion,'speak'),patch.object(companion,'INPUT_MODE','mixed'):
                self.assertEqual(companion.handle('leave document','document'),'document_save')
                self.assertEqual(companion.handle('yes','document_save'),'document_name')
                self.assertEqual(companion.handle('Report for Will','document_name',typed=True),'awake')
                self.assertTrue((Path(folder)/'Report for Will.docx').exists())
                self.assertEqual(document_text(doc),'Body stays separate.')
                self.assertFalse((Path(folder)/'Untitled.docx').exists())

@unittest.skipUnless(sys.platform=='win32','Windows Tk keyboard integration')
class WindowKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.window=CompanionWindow('keyboard test');self.window.start()
    def tearDown(self):
        self.window.closed.set();self.call(lambda:self.window.root.destroy())
        self.window.thread.join(5);self.assertFalse(self.window.thread.is_alive())
    def call(self,function):
        done=threading.Event();errors=[]
        def run():
            try:function()
            except Exception as exc:errors.append(exc)
            finally:done.set()
        self.window.ui_actions.put(('ui_call',run));self.assertTrue(done.wait(5))
        if errors:raise errors[0]
    def wait(self,condition):
        end=time.monotonic()+5
        while not condition() and time.monotonic()<end:time.sleep(.015)
        self.assertTrue(condition())
    def context(self,mode='document',readonly=False):
        self.window.set_context(dict(mode=mode,source='test',text='abc def',caret=0,selection=None,echo='characters',phonetic=True,delay=.5,ack=0,readonly=readonly))
        self.wait(lambda:self.window.context.get('source')=='test')
        self.call(lambda:self.window.editor.focus_force())
    def test_right_arrow_echo_is_immediate_and_phonetic_is_delayed(self):
        self.context();started=time.monotonic()
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Right'))
        self.wait(lambda:not self.window.key_feedback.empty())
        self.assertEqual(self.window.key_feedback.get(),'b');self.assertLess(time.monotonic()-started,.4)
        time.sleep(.2);self.assertTrue(self.window.key_feedback.empty())
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'Bravo')
    def test_pending_phonetic_is_canceled_when_speech_is_silenced(self):
        self.context()
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Right'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'b')
        self.call(lambda:self.window.ui_actions.put(('cancel_phonetic',None)))
        time.sleep(.65);self.assertTrue(self.window.key_feedback.empty())
    def test_return_to_menu_clears_stale_field_and_restores_arrow_navigation(self):
        self.context();self.window.email_field='recipient'
        self.window.set_context(dict(mode='awake',source=None,text='',echo='characters',phonetic=False,delay=.5,ack=0))
        self.wait(lambda:self.window.context['mode']=='awake')
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='Down'))
        self.wait(lambda:not self.window.commands.empty())
        self.assertEqual(self.window.commands.get(),('keyboard','Down'));self.assertIsNone(self.window.email_field)
    def test_returning_from_settings_does_not_reuse_old_edit_sequence(self):
        self.context()
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Right'))
        self.wait(lambda:not self.window.commands.empty())
        first=self.window.commands.get()[1]['seq']
        self.window.set_context(dict(mode='settings',source=None,text='',echo='characters',phonetic=False,delay=.5,ack=0))
        self.wait(lambda:self.window.context['mode']=='settings')
        self.context()
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Right'))
        self.wait(lambda:not self.window.commands.empty())
        self.assertGreater(self.window.commands.get()[1]['seq'],first)
    def test_last_edit_is_queued_before_escape_or_settings_transition(self):
        for event,command in (('<Escape>','Escape'),('<Control-comma>','settings')):
            with self.subTest(event=event):
                self.window.set_context(dict(mode='awake',source=None,text='',echo='characters',phonetic=False,delay=.5,ack=0))
                self.context()
                self.call(lambda ev=event:(self.window.editor.insert('insert','z'),self.window.editor.event_generate(ev)))
                self.wait(lambda:self.window.commands.qsize()>=2)
                first=self.window.commands.get();second=self.window.commands.get()
                self.assertEqual(first[0],'text_edit');self.assertEqual(first[1]['after'],'zabc def')
                self.assertEqual(second,('keyboard',command))
                while not self.window.commands.empty():self.window.commands.get()
    def test_f2_flushes_last_edit_before_naming(self):
        self.context()
        def edit_and_name():
            self.window.editor.insert('insert','Z')
            self.window.editor.event_generate('<KeyPress>',keysym='F2')
        self.call(edit_and_name)
        self.wait(lambda:self.window.commands.qsize()>=2)
        first=self.window.commands.get();second=self.window.commands.get()
        self.assertEqual(first[0],'text_edit');self.assertTrue(first[1]['after'].startswith('Z'))
        self.assertEqual(second,('keyboard','F2'))
    def test_mail_shortcut_works_in_received_message_without_mutating_body(self):
        self.window.set_context(dict(mode='mailbox',source='mail:test',text='Received body.',readonly=True,caret=0,selection=None,echo='characters',phonetic=False,delay=.5,ack=0,focus=True))
        self.call(lambda:self.window.editor.event_generate('<Control-KeyPress-r>'))
        self.wait(lambda:not self.window.commands.empty())
        self.assertEqual(self.window.commands.get(),('keyboard','Ctrl+R'))
        body=[];self.call(lambda:body.append(self.window.editor.get('1.0','end-1c')))
        self.assertEqual(body,['Received body.'])
    def test_save_confirmation_arrows_enter_and_escape_dispatch_from_field(self):
        self.window.set_context(dict(mode='document_save',source=None,text='',confirmation='yes',echo='characters',phonetic=False,delay=.5,ack=0,focus=True))
        for key,result in (('Down','Down'),('Return','Enter'),('Escape','Escape')):
            self.call(lambda k=key:self.window.typed.event_generate('<KeyPress>',keysym=k))
            self.wait(lambda:not self.window.commands.empty())
            self.assertEqual(self.window.commands.get(),('keyboard',result))
    def test_keyboard_typing_emits_literal_edit_and_immediate_echo(self):
        self.context()
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='z'))
        self.wait(lambda:not self.window.commands.empty())
        edits=[]
        while not self.window.commands.empty():edits.append(self.window.commands.get())
        self.assertTrue(any(kind=='text_edit' and value['after']=='zabc def' for kind,value in edits))
        self.wait(lambda:not self.window.key_feedback.empty())
        self.assertEqual(self.window.key_feedback.get(),'z')
    def test_control_down_moves_by_paragraph_and_speaks_it(self):
        self.context()
        self.window.set_context(dict(mode='document',source='test',text='First paragraph.\nSecond paragraph.',caret=0,selection=None,echo='characters',phonetic=False,delay=.5,ack=0))
        self.call(lambda:self.window.editor.event_generate('<Control-KeyPress-Down>'))
        self.wait(lambda:not self.window.key_feedback.empty())
        self.assertEqual(self.window.key_feedback.get(),'Second paragraph.')
    def test_all_menu_arrows_dispatch_without_spurious_empty_field_echo(self):
        self.window.set_context(dict(mode='awake',source=None,text='',echo='characters',phonetic=True,delay=.5,ack=0,focus=True))
        self.call(lambda:self.window.typed.focus_force())
        for key in ('Up','Down','Left','Right'):
            self.call(lambda k=key:self.window.typed.event_generate('<KeyPress>',keysym=k))
            self.wait(lambda:not self.window.commands.empty())
            self.assertEqual(self.window.commands.get(),('keyboard',key))
        self.assertTrue(self.window.key_feedback.empty())
    def test_spoken_name_is_prefilled_and_edits_survive_settings_before_enter(self):
        naming=dict(mode='document_name',source=None,text='',echo='characters',phonetic=False,delay=.5,ack=0,naming_request=(1,'Spoken report'))
        self.window.set_context(naming)
        values=[];self.call(lambda:values.append(self.window.typed.get()));self.assertEqual(values,['Spoken report'])
        self.call(lambda:(self.window.typed.delete(0,'end'),self.window.typed.insert(0,'Edited report')))
        self.window.set_context(dict(mode='settings',source=None,text='',echo='characters',phonetic=False,delay=.5,ack=0))
        self.call(lambda:None);self.window.set_context(naming)
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='Return'))
        self.wait(lambda:not self.window.commands.empty());self.assertEqual(self.window.commands.get(),'Edited report')
    def test_empty_naming_field_arrows_cannot_become_filenames(self):
        self.window.set_context(dict(mode='document_name',source=None,text='',echo='characters',phonetic=False,delay=.5,ack=0))
        self.call(lambda:self.window.typed.focus_force())
        for key in ('Up','Down','Left','Right'):self.call(lambda k=key:self.window.typed.event_generate('<KeyPress>',keysym=k))
        self.assertTrue(self.window.commands.empty())
    def test_tab_does_not_insert_into_received_mail(self):
        self.context('mailbox',True)
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Tab'))
        values=[];self.call(lambda:values.append(self.window.editor.get('1.0','end-1c')))
        self.assertEqual(values,['abc def'])
    def test_readonly_message_moves_cursor_but_does_not_accept_typing(self):
        self.context('mailbox',True)
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Right'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'b')
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='z'))
        values=[];self.call(lambda:values.append(self.window.editor.get('1.0','end-1c')))
        self.assertEqual(values,['abc def'])

if __name__=='__main__':unittest.main()


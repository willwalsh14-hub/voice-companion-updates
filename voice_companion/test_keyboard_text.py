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
    def test_typed_document_name_is_literal_even_when_it_matches_an_app_command(self):
        with tempfile.TemporaryDirectory() as folder:
            for title in ('Options','Exit companion','Go to sleep','Save as a title'):
                doc=VoiceDocument(Path(folder))
                window=CompanionWindow('test');window.context['mode']='document_name'
                window.submit_entry(title);command=window.commands.get_nowait()
                self.assertEqual(command,'name document '+title)
                with patch.object(companion,'document',doc),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'speak'),patch.object(companion,'DOCUMENT_NAME_RETURN','document'),patch.object(companion,'sync_app_context'):
                    self.assertEqual(companion.handle_keyboard(command,'document_name'),'document')
                    self.assertEqual(doc.title,title)
    def test_file_list_widget_is_released_on_its_owner_thread(self):
        released=[]
        class WidgetReference:
            def __del__(self):released.append(threading.get_ident())
        window=CompanionWindow('cleanup test')
        window._run=lambda:setattr(window,'file_list',WidgetReference())
        window.thread.start();window.thread.join(5)
        self.assertFalse(window.thread.is_alive())
        self.assertEqual(released,[window.thread.ident])
        self.assertIsNone(window.file_list)

    def test_builder_script_answers_document_save_question_before_email(self):
        import io
        source=(Path(companion.__file__).parent/'build-windows.ps1').read_text()
        line=next(line for line in source.splitlines() if line.strip().startswith('$testCommands ='))
        commands=line.split('"',2)[1].replace('`n','\n')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(companion,'SLEEP_RETURN_MODE','awake'),patch.object(companion,'document',None),patch.object(companion,'email_draft',None),patch.object(companion,'mail_session',None),patch.object(companion,'KEYBOARD_DIRTY',{}),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'INPUT_MODE','mixed'),patch.object(companion,'APP',root),patch.object(companion,'TEXT_MODE',True),patch.object(companion.sys,'stdin',io.StringIO(commands)),patch.object(companion.sys,'argv',['companion.py','--text-mode']),patch.object(companion,'speak') as speech:
                self.assertEqual(companion.main(),0)
                spoken=' '.join(call.args[0] for call in speech.call_args_list)
                self.assertIn('Save this document?',spoken)
                self.assertIn('This is a local email draft',spoken)
                self.assertIn('It has not been sent',spoken)
                self.assertTrue((root/'Documents'/'Build check.docx').exists())
                self.assertTrue(list((root/'Email Drafts').glob('*.eml')))
    def test_keyboard_menu_and_document_exit_work_in_dictation_only(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.append_text('Keep this body.')
            with patch.object(companion,'document',doc),patch.object(companion,'INPUT_MODE','dictation'),patch.object(companion,'speak'),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'MAIN_MENU_INDEX',None):
                self.assertEqual(companion.handle_keyboard('next','awake'),'awake')
                self.assertEqual(companion.MAIN_MENU_INDEX,0)
                for typed in (False,True):
                    self.assertEqual(companion.handle('main menu','document',typed=typed),'document_save')
                    self.assertEqual(document_text(doc),'Keep this body.')
    def test_document_save_no_restores_existing_file_and_removes_new_file(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder),title='Existing');doc.append_text('Original.');original=doc.path.read_bytes()
            with patch.object(companion,'document',doc),patch.object(companion,'speak'),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'DOCUMENT_BASELINE',None):
                companion.checkpoint_document();doc.append_text('Changed.')
                self.assertNotEqual(doc.path.read_bytes(),original)
                self.assertEqual(companion.handle('main menu','document'),'document_save')
                self.assertEqual(companion.handle('no','document_save'),'awake')
                self.assertEqual(doc.path.read_bytes(),original)
                self.assertIsNone(companion.document)
                fresh=VoiceDocument(Path(folder))
                companion.document=fresh;companion.checkpoint_document();fresh.append_text('Discard me.')
                self.assertEqual(companion.handle('leave document','document'),'document_save')
                self.assertEqual(companion.handle('no','document_save'),'word_processor');self.assertFalse(fresh.path.exists())
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
                    self.assertEqual(result,'word_processor');self.assertTrue((Path(folder)/'Spoken report.docx').exists())
                    self.assertEqual(document_text(doc),'Separate body.')
    def test_untitled_exit_prompts_and_name_is_not_document_text(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Body stays separate.')]
            with patch.object(companion,'document',doc),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'KEYBOARD_DIRTY',{}),patch.object(companion,'speak'),patch.object(companion,'INPUT_MODE','mixed'):
                self.assertEqual(companion.handle('leave document','document'),'document_save')
                self.assertEqual(companion.handle('yes','document_save'),'document_name')
                self.assertEqual(companion.handle('Report for Will','document_name',typed=True),'word_processor')
                self.assertTrue((Path(folder)/'Report for Will.docx').exists())
                self.assertEqual(document_text(doc),'Body stays separate.')
                self.assertFalse((Path(folder)/'Untitled.docx').exists())

@unittest.skipUnless(sys.platform=='win32','Windows Tk keyboard integration')
class WindowKeyboardTests(unittest.TestCase):
    def test_document_command_shortcuts_are_routed_before_text_editing(self):
        self.window.set_context(dict(mode='document',source='shortcut-document',text='Keep this text',caret=0,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('source')=='shortcut-document')
        for key,state,expected in [('s',4,'Ctrl+S'),('b',4,'Ctrl+B'),('o',4,'Ctrl+O'),('p',5,'Ctrl+Shift+P'),('Return',4,'Ctrl+Enter'),('Alt_L',0,'Alt'),('F4',1,'Shift+F4')]:
            self.call(lambda k=key,s=state:self.window.editor.event_generate('<KeyPress>',keysym=k,state=s))
            self.wait(lambda e=expected:('keyboard',e) in self.window.commands.queue)
        self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'Keep this text'))
        self.window.set_context(dict(mode='document',source='shortcut-document',text='Keep this text',caret=0,ack=0,document_table=True))
        self.wait(lambda:self.window.context.get('document_table'))
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Tab'))
        self.wait(lambda:('keyboard','Doc:NextCell') in self.window.commands.queue)
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Tab',state=1))
        self.wait(lambda:('keyboard','Doc:PreviousCell') in self.window.commands.queue)

    def test_file_manager_ctrl_d_replaces_old_drive_and_main_shortcuts(self):
        self.window.set_context(dict(mode='files',source=None,text='',file_items=['C drive'],file_index=0,file_selected_indices=[],ack=0,focus=True))
        self.wait(lambda:self.window.context.get('mode')=='files')
        self.call(lambda:self.window.file_list.event_generate('<KeyPress>',keysym='d',state=4))
        self.wait(lambda:('keyboard','Ctrl+D') in self.window.commands.queue)
        self.call(lambda:self.window.file_list.event_generate('<KeyPress>',keysym='Home',state=4))
        self.call(lambda:self.window.file_list.event_generate('<KeyPress>',keysym='m',state=4))
        self.assertNotIn(('keyboard','Ctrl+Home'),self.window.commands.queue)
        self.assertNotIn(('keyboard','Ctrl+M'),self.window.commands.queue)

    def setUp(self):
        self.window=CompanionWindow('keyboard test');self.window.start()
    def test_document_screen_reader_chords_and_heading_keys_preserve_text(self):
        self.context()
        def keys():
            editor=self.window.editor
            for modifier,key,state in [('Insert','f',0),('Insert','Down',1),('Caps_Lock','f',0),('Caps_Lock','Down',0)]:
                editor.event_generate('<KeyPress>',keysym=modifier)
                editor.event_generate('<KeyPress>',keysym=key,state=state)
                editor.event_generate('<KeyRelease>',keysym=modifier)
            editor.event_generate('<KeyPress>',keysym='2',state=12)
        self.call(keys)
        self.wait(lambda:('keyboard','Ctrl+Alt+2') in self.window.commands.queue)
        for chord in ('Insert+F','Insert+Shift+Down','CapsLock+F','CapsLock+Down'):
            self.assertIn(('keyboard',chord),self.window.commands.queue)
        self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'abc def'))

    def test_file_search_shortcut_from_each_toolbar_control_and_input(self):
        self.window.set_context(dict(mode='files',source=None,file_items=['Music.mp3, file'],file_index=0,focus=True))
        self.wait(lambda:self.window.context.get('mode')=='files')
        for widget in [self.window.file_list,*self.window.file_buttons,self.window.typed]:
            self.call(lambda w=widget:w.event_generate('<KeyPress>',keysym='e',state=4))
        self.wait(lambda:self.window.commands.qsize()>=8)
        self.assertEqual(list(self.window.commands.queue).count(('keyboard','Ctrl+E')),8)

    def test_update_buttons_announce_focus_activate_and_have_shortcuts(self):
        self.window.set_context(dict(mode='update_download',source=None,focus=True))
        self.wait(lambda:self.window.context.get('mode')=='update_download')
        self.call(lambda:self.window.typed.event_generate('<Tab>'))
        self.call(lambda:self.assertIs(self.window.root.focus_get(),self.window.update_buttons[0]))
        self.call(lambda:self.window.update_buttons[0].event_generate('<Return>'))
        self.call(lambda:self.window.update_buttons[0].event_generate('<Tab>'))
        self.call(lambda:self.window.update_buttons[1].event_generate('<Return>'))
        self.assertIn('update status',self.window.commands.queue);self.assertIn('cancel update',self.window.commands.queue)
        self.assertTrue(any('Status button' in text for text in self.window.key_feedback.queue))
        self.assertTrue(any('Cancel update button' in text for text in self.window.key_feedback.queue))

    def test_file_toolbar_tab_order_and_typeahead_search_sort_keys(self):
        self.window.set_context(dict(mode='files',source=None,text='',file_items=['Alpha, folder','Beta, file'],file_index=0,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('mode')=='files')
        self.call(lambda:self.window.file_list.focus_force())
        def exercise():
            widget=self.window.file_list
            widget.event_generate('<KeyPress>',keysym='a')
            widget.event_generate('<KeyPress>',keysym='l')
            widget.event_generate('<Tab>')
        self.call(exercise)
        def activate():
            self.assertIs(self.window.root.focus_get(),self.window.file_buttons[0])
            self.window.file_buttons[0].event_generate('<Return>')
            self.window.file_buttons[0].event_generate('<Shift-Tab>')
        self.call(activate)
        def sort_keys():
            self.assertIs(self.window.root.focus_get(),self.window.file_list)
            self.window.file_list.event_generate('<KeyPress>',keysym='3',state=5)
            self.window.file_list.event_generate('<KeyPress>',keysym='s',state=8)
        self.call(sort_keys)
        self.wait(lambda:('keyboard','Alt+S') in self.window.commands.queue)
        for command in (('keyboard','Letter:a'),('keyboard','Letter:l'),'search files',('keyboard','Ctrl+Shift+3')):
            self.assertIn(command,self.window.commands.queue)
    def tearDown(self):
        if self.window.thread.is_alive():self.call(lambda:self.window.root.destroy())
        self.window.thread.join(5);self.assertFalse(self.window.thread.is_alive())
    def test_file_list_routes_selection_clipboard_menu_and_rename_keys(self):
        context=dict(mode='files',source=None,text='',file_items=['C drive, drive','Documents, folder','notes.txt, file'],file_index=1,file_selected_indices=[0,2],echo='none',phonetic=False,ack=0,focus=True)
        self.window.set_context(context);self.wait(lambda:self.window.context.get('mode')=='files')
        self.call(lambda:self.window.file_list.focus_force())
        self.call(lambda:self.assertEqual(tuple(self.window.file_list.curselection()),(0,2)))
        self.call(lambda:self.window.file_list.event_generate('<KeyPress>',keysym='t',state=8))
        self.wait(lambda:self.window.command_entry_active)
        self.call(lambda:self.window.typed.focus_force())
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='Escape'))
        self.assertFalse(self.window.command_entry_active)
        for key,state,expected in [('Down',0,'Down'),('space',0,'Space'),('c',4,'Ctrl+C'),('x',4,'Ctrl+X'),('v',4,'Ctrl+V'),('a',5,'Ctrl+Shift+A'),('Alt_L',0,'Alt'),('F2',0,'F2')]:
            self.call(lambda k=key,s=state:self.window.file_list.event_generate('<KeyPress>',keysym=k,state=s))
            self.wait(lambda e=expected:('keyboard',e) in self.window.commands.queue)
        while not self.window.commands.empty():self.window.commands.get_nowait()
        self.window.set_context(dict(context,file_name_input=True,file_rename_value='notes.txt',menu_letters=False))
        self.wait(lambda:self.window.context.get('file_name_input'))
        self.call(lambda:self.assertEqual(self.window.typed.get(),'notes.txt'))
        self.call(lambda:(self.window.typed.delete(0,'end'),self.window.typed.insert(0,'renamed.txt'),self.window.typed.event_generate('<KeyPress>',keysym='Return')))
        self.wait(lambda:not self.window.commands.empty())
        self.assertEqual(self.window.commands.get_nowait(),'renamed.txt')

    def test_page_revision_replaces_stale_view_despite_pending_caret_ack(self):
        initial=dict(mode='web',source='page',text='Email screen',caret=0,ack=0,readonly=True,web_browse=True,web_revision=1,web_cursor_revision=1)
        self.window.set_context(initial);self.call(lambda:self.window.editor.focus_force())
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='a',state=4))
        self.wait(lambda:not self.window.commands.empty());self.window.commands.get()
        self.window.set_context(initial|{'text':'Password screen','web_revision':2})
        self.wait(lambda:self.window.context.get('web_revision')==2)
        self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'Password screen'))

    def test_forced_text_refresh_recovers_unacknowledged_edit_then_arrows(self):
        self.context()
        self.call(lambda:self.window.editor.insert('end',' old pending'))
        self.wait(lambda:not self.window.commands.empty())
        self.window.set_context(dict(self.window.context,text='New dictated sentence.',caret=22,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('text')=='New dictated sentence.')
        self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'New dictated sentence.'))
        while not self.window.commands.empty():self.window.commands.get_nowait()
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Left'))
        self.wait(lambda:not self.window.commands.empty())
        action,payload=self.window.commands.get_nowait()
        self.assertEqual(action,'text_edit')
        self.assertEqual(payload['before'],'New dictated sentence.')
        self.assertEqual(payload['after'],'New dictated sentence.')

    def test_dynamic_page_refresh_does_not_raise_window_or_force_focus(self):
        initial=dict(mode='web',source='same-page',text='Initial page',caret=0,ack=0,readonly=True,web_browse=True,web_revision=1)
        self.window.set_context(initial);self.wait(lambda:self.window.context.get('web_revision')==1)
        calls=[]
        def observe_focus():
            self.window.root.lift=lambda:calls.append('raise')
            self.window.root.deiconify=lambda:calls.append('restore')
            self.window.editor.focus_force=lambda:calls.append('focus')
        self.call(observe_focus)
        self.window.set_context(initial|{'text':'Updated page','web_revision':2})
        self.wait(lambda:self.window.context.get('web_revision')==2);self.call(lambda:None)
        self.assertEqual(calls,[])
    def test_empty_document_name_accepts_letters_and_editing_keys_without_commands(self):
        self.window.set_context(dict(mode='document_name',source=None,text='',caret=0,menu_letters=True,echo='none',ack=0))
        self.wait(lambda:self.window.context['mode']=='document_name')
        def type_name():
            entry=self.window.typed
            entry.focus_force()
            for key in ('h','e','l','p','Left','Right','Home','End'):
                entry.event_generate('<KeyPress>',keysym=key)
        self.call(type_name)
        self.call(lambda:None)
        self.assertTrue(self.window.commands.empty())
        values=[];self.call(lambda:values.append(self.window.typed.get()))
        self.assertEqual(values,['help'])
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='Return'))
        self.wait(lambda:not self.window.commands.empty())
        self.assertEqual(self.window.commands.get_nowait(),'name document help')

    def test_shutdown_discards_delayed_view_updates_and_readback(self):
        def close_with_pending_readback():
            self.window.messages.put('Delayed readback')
            self.window.ui_actions.put(('context',{'mode':'web','source':'closed-page','text':'Old page','caret':0}))
            self.window.root.destroy()
        self.call(close_with_pending_readback)
        self.window.thread.join(5)
        self.assertTrue(self.window.closed.is_set());self.assertFalse(self.window.thread.is_alive());self.assertIsNone(self.window.problem)
    def call(self,function):
        done=threading.Event();errors=[]
        def run():
            try:
                # Geometry is settled before a real key can reach a newly
                # shown widget; synthetic events need the same barrier.
                self.window.root.update_idletasks()
                function()
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
    def test_web_activation_ignores_held_key_repeats_but_allows_the_next_press(self):
        self.context(mode='web',readonly=True)
        self.window.set_context({**self.window.context,'web_browse':True})
        self.wait(lambda:self.window.context.get('web_browse'))
        for key,command in (('space','Web:Space'),('Return','Web:Activate')):
            def held():
                for _ in range(4):self.window.editor.event_generate('<KeyPress>',keysym=key)
            self.call(held)
            self.wait(lambda:('keyboard',command) in self.window.commands.queue)
            events=[]
            while not self.window.commands.empty():events.append(self.window.commands.get_nowait())
            self.assertEqual(events.count(('keyboard',command)),1)
            self.call(lambda:self.window.editor.event_generate('<KeyRelease>',keysym=key))
            self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym=key))
            self.wait(lambda:('keyboard',command) in self.window.commands.queue)
            self.assertEqual(self.window.commands.get_nowait(),('keyboard',command))
            self.call(lambda:self.window.editor.event_generate('<KeyRelease>',keysym=key))

    def test_enter_in_single_line_web_field_queues_one_default_action(self):
        self.window.set_context(dict(mode='web',source='default-submit',text='person@example.com',caret=0,readonly=False,web_field={'key':'email','role':'field','tag':'input','type':'email'},echo='none',phonetic=False,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('source')=='default-submit')
        self.call(self.window.editor.focus_force)
        self.call(lambda:[self.window.editor.event_generate('<KeyPress>',keysym='Return') for _ in range(3)])
        self.wait(lambda:('keyboard','Web:Done') in self.window.commands.queue)
        events=[]
        while not self.window.commands.empty():events.append(self.window.commands.get_nowait())
        self.assertEqual(events.count(('keyboard','Web:Done')),1)
        self.call(lambda:self.window.editor.event_generate('<KeyRelease>',keysym='Return'))

    def test_web_quick_keys_tables_and_reading_editor(self):
        self.context(mode='web',readonly=True)
        self.window.set_context({**self.window.context,'web_browse':True})
        self.wait(lambda:self.window.context.get('web_browse'))
        for key,state,expected in [('k',0,'Web:Quick:k'),('h',1,'Web:Quick:Previous:h'),('Right',12,'Web:Table:Right'),('Tab',0,'Web:Tab'),('Escape',0,'Web:Escape')]:
            self.call(lambda k=key,s=state:self.window.editor.event_generate('<KeyPress>',keysym=k,state=s))
            self.wait(lambda e=expected:('keyboard',e) in self.window.commands.queue)
        self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'abc def'))
    def test_virtual_page_quick_keys_and_arrows_work_from_editor_entry_and_root(self):
        self.window.set_context(dict(mode='web',source='page',text='Heading\nOrdinary body text\nLink',caret=0,ack=0,readonly=True,web_browse=True,web_revision=1))
        self.wait(lambda:self.window.context.get('web_browse'))
        for widget_name in ('editor','typed','root'):
            for key,state,expected in [('h',0,'Web:Quick:h'),('h',1,'Web:Quick:Previous:h'),('Down',0,'Web:Read:Down'),('Up',0,'Web:Read:Up')]:
                with self.subTest(widget=widget_name,key=key,state=state):
                    def press():
                        widget=getattr(self.window,widget_name);widget.focus_force()
                        if widget_name=='typed':widget.insert(0,'stray entry')
                        widget.event_generate('<KeyPress>',keysym=key,state=state)
                    self.call(press)
                    self.wait(lambda e=expected:('keyboard',e) in self.window.commands.queue)
                    while not self.window.commands.empty():self.window.commands.get()
        self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'Heading\nOrdinary body text\nLink'))
        self.call(lambda:self.assertEqual(self.window.editor.cget('state'),'disabled'))

    def test_escape_followed_immediately_by_h_leaves_editing_without_typing_h(self):
        field={'key':'name','tag':'input','type':'text','value':'Original','label':'Name'}
        self.window.set_context(dict(mode='web',source='field',text='Original',caret=0,ack=0,readonly=False,web_browse=False,web_field=field))
        self.wait(lambda:self.window.context.get('web_field')==field)
        def press():
            self.window.editor.focus_force()
            self.window.editor.event_generate('<KeyPress>',keysym='Escape')
            self.window.editor.event_generate('<KeyPress>',keysym='h')
        self.call(press)
        self.wait(lambda:self.window.commands.qsize()>=2)
        self.assertEqual(self.window.commands.get(),('keyboard','Web:Escape'))
        self.assertEqual(self.window.commands.get(),('keyboard','Web:Quick:h'))
        self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'Original'))

    def test_browser_menu_typeahead_and_literal_bookmark_name(self):
        self.window.set_context(dict(mode='browser_menu',source=None,text='',menu_letters=True,browser_name_input=False))
        self.wait(lambda:self.window.context.get('mode')=='browser_menu')
        self.call(lambda:[self.window.typed.event_generate('<KeyPress>',keysym=letter) for letter in ('n','p')])
        self.wait(lambda:self.window.commands.qsize()>=2)
        self.assertEqual(self.window.commands.get(),('keyboard','Letter:n'))
        self.assertEqual(self.window.commands.get(),('keyboard','Letter:p'))
        self.call(lambda:self.assertEqual(self.window.typed.get(),''))
        self.window.set_context(dict(mode='browser_menu',source=None,text='',menu_letters=False,browser_name_input=True))
        self.wait(lambda:self.window.context.get('browser_name_input'))
        self.call(lambda:[self.window.typed.event_generate('<KeyPress>',keysym=letter) for letter in 'help'])
        self.call(lambda:self.assertEqual(self.window.typed.get(),'help'))
        self.assertTrue(self.window.commands.empty())
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='Return'))
        self.wait(lambda:not self.window.commands.empty());self.assertEqual(self.window.commands.get(),'help')

    def test_browser_alt_arrows_never_become_reading_arrows(self):
        for editing in (False,True):
            self.window.set_context(dict(mode='web',source='page',text='Page text',caret=0,ack=0,readonly=not editing,web_browse=not editing,web_field={'key':'name','type':'text'} if editing else None))
            self.wait(lambda:self.window.context.get('web_field') is not None if editing else self.window.context.get('web_browse'))
            for key,request in (('Left','Web:Back'),('Right','Web:Forward')):
                self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym=key,state=8))
                self.wait(lambda:not self.window.commands.empty())
                self.assertEqual(self.window.commands.get(),('keyboard',request))

    def test_explicit_alt_t_allows_a_command_and_escape_returns_to_page(self):
        self.window.set_context(dict(mode='web',source='page',text='Page text',caret=0,ack=0,readonly=True,web_browse=True))
        self.wait(lambda:self.window.context.get('web_browse'))
        def enter_command():
            self.window.editor.event_generate('<Alt-KeyPress-t>')
            self.window.typed.event_generate('<KeyPress>',keysym='h')
        self.call(enter_command)
        self.assertTrue(self.window.command_entry_active)
        self.call(lambda:self.assertEqual(self.window.typed.get(),'h'))
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='Escape'))
        self.assertFalse(self.window.command_entry_active)
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='h'))
        self.wait(lambda:('keyboard','Web:Quick:h') in self.window.commands.queue)
        self.call(lambda:self.assertEqual(self.window.typed.get(),''))

    def test_delete_feedback_in_text_editor_and_typed_search(self):
        self.context()
        self.call(lambda:self.window.editor.mark_set('insert','1.2'))
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='BackSpace'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'b')
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Delete'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'space')
        self.window.set_context(dict(mode='search',source=None,text='',caret=0,echo='characters',phonetic=False,ack=0,focus=True))
        self.wait(lambda:self.window.context['mode']=='search')
        self.call(lambda:(self.window.typed.insert(0,'abc'),self.window.typed.icursor(2)))
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='BackSpace'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'b')
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='Delete'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'End of text.')
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='Return'))
        self.wait(lambda:('web_search_query','a') in self.window.commands.queue)
    def test_web_choice_keys_do_not_type_or_require_explicit_activation(self):
        self.window.set_context(dict(mode='web',source='web-choice',text='Alpha',caret=0,readonly=True,web_choice=True,web_field={'key':'choice','role':'combobox','tag':'select'},echo='characters',phonetic=False,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('source')=='web-choice');self.call(self.window.editor.focus_force)
        for key,state,request in (('Down',0,'Web:Down'),('b',0,'Web:ChoiceLetter:b'),('Home',4,'Web:PageTop'),('End',4,'Web:PageBottom'),('Tab',0,'Web:Tab')):
            self.call(lambda k=key,s=state:self.window.editor.event_generate('<KeyPress>',keysym=k,state=s))
            self.wait(lambda r=request:('keyboard',r) in self.window.commands.queue)
        self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'Alpha'))
    def test_single_line_web_edit_arrows_leave_field(self):
        self.window.set_context(dict(mode='web',source='web-field',text='Typed',caret=0,readonly=False,web_field={'key':'name','role':'field','tag':'input','type':'text'},echo='characters',phonetic=False,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('source')=='web-field');self.call(self.window.editor.focus_force)
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Down'))
        self.wait(lambda:('keyboard','Web:Leave:Down') in self.window.commands.queue)
    def test_html_email_roles_quick_keys_and_activation(self):
        from email_reader import render_html
        body=render_html('<p>First</p><a href="https://example.com/donate"><img alt="Click to donate"></a><p><a role="button" href="https://example.com/give">Donate now</a></p>')
        self.window.set_context(dict(mode='mailbox',source='mail:test',text=body,rich_spans=body.spans,email_html=True,readonly=True,caret=0,echo='characters',phonetic=False,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('source')=='mail:test')
        self.call(lambda:self.window.editor.focus_force())
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Down'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertIn('Click to donate, link',self.window.key_feedback.get())
        self.call(lambda:self.window.editor.mark_set('insert','1.0'))
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Tab'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertIn('Click to donate, link',self.window.key_feedback.get())
        with patch('webbrowser.open') as opener:
            self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Return'))
            opener.assert_called_once_with('https://example.com/donate')
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='b'))
        self.wait(lambda:('keyboard','Email:next button') in self.window.commands.queue)
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='k',state=1))
        self.wait(lambda:('keyboard','Email:previous link') in self.window.commands.queue)
    def test_help_uses_reading_editor_and_escape_dispatches(self):
        self.context(mode='help',readonly=True)
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Right'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'b')
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Escape'))
        self.wait(lambda:any(item==('keyboard','Escape') for item in self.window.commands.queue))

    def test_help_arrows_dispatch_blocks_without_native_line_speech(self):
        self.context(mode='help',readonly=True)
        self.window.set_context(self.window.context|{'help_reader':True})
        self.wait(lambda:self.window.context.get('help_reader'))
        self.call(lambda:self.window.editor.focus_force())
        for key in ('Down','Up','Home','End'):
            self.call(lambda k=key:self.window.editor.event_generate('<KeyPress>',keysym=k))
            self.wait(lambda k=key:('keyboard','Help:'+k) in self.window.commands.queue)
        self.assertTrue(self.window.key_feedback.empty())
        self.assertFalse(any(isinstance(item,tuple) and item[0]=='text_edit' for item in self.window.commands.queue))

    def test_web_semantic_quick_keys_and_link_list_name_typing(self):
        self.window.set_context(dict(mode='web',source='page',text='Page',caret=0,readonly=True,ack=0,web_browse=True))
        self.wait(lambda:self.window.context.get('web_browse'))
        for key in ('k','l','i','g','d','m'):
            for state in (0,1):
                self.call(lambda k=key,s=state:self.window.editor.event_generate('<KeyPress>',keysym=k,state=s))
                expected='Web:Quick:'+('Previous:' if state else '')+key
                self.wait(lambda e=expected:('keyboard',e) in self.window.commands.queue)
        self.window.set_context(dict(mode='web',source=None,text='',caret=0,ack=0,web_name_list='links'))
        self.wait(lambda:self.window.context.get('web_name_list')=='links')
        for key,char in [('a','a'),('c','c'),('space',' ')]:
            self.call(lambda k=key:self.window.typed.event_generate('<KeyPress>',keysym=k))
            self.wait(lambda c=char:('keyboard','Web:ListText:'+c) in self.window.commands.queue)
        self.call(lambda:self.assertEqual(self.window.typed.get(),''))
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='BackSpace'))
        self.wait(lambda:('keyboard','Web:ListBackspace') in self.window.commands.queue)
    def test_menu_home_end_and_confirmation_letters_dispatch_immediately(self):
        self.window.set_context(dict(mode='awake',source=None,menu_letters=True,text='',caret=0,ack=0))
        self.wait(lambda:self.window.context.get('mode')=='awake')
        self.call(lambda:self.window.typed.focus_force())
        for key in ('Home','End'):
            self.call(lambda key=key:self.window.typed.event_generate('<KeyPress>',keysym=key))
            self.wait(lambda key=key:('keyboard',key) in self.window.commands.queue)
        self.window.set_context(dict(mode='exit_confirm',source=None,confirmation='no',text='',caret=0,ack=0))
        self.wait(lambda:self.window.context.get('mode')=='exit_confirm')
        for key in ('y','n','Control_L'):
            self.call(lambda key=key:self.window.typed.event_generate('<KeyPress>',keysym=key))
            expected='Control' if key=='Control_L' else key
            self.wait(lambda expected=expected:('keyboard',expected) in self.window.commands.queue)
        self.call(lambda:self.assertEqual(self.window.typed.get(),''))
    def test_alt_yes_no_confirmations_override_editor_and_typed_fields(self):
        for mode in ('exit_confirm','power_confirm','update_offer','email_draft','web'):
            source='confirm-'+mode if mode in ('email_draft','web') else None
            self.window.set_context(dict(mode=mode,source=source,text='Preserved text' if source else '',readonly=mode=='web',confirmation='no',caret=0,ack=0,focus=True))
            self.wait(lambda:self.window.context.get('mode')==mode)
            for widget in ((self.window.editor,self.window.typed) if source else (self.window.typed,)):
                self.call(lambda widget=widget:widget.focus_force())
                if widget==self.window.typed:self.call(lambda:(self.window.typed.delete(0,'end'),self.window.typed.insert(0,'Preserved header')))
                for key,expected in (('y','Alt+Y'),('n','Alt+N')):
                    self.call(lambda key=key,widget=widget:widget.event_generate('<KeyPress>',keysym=key,state=8))
                    self.wait(lambda expected=expected:('keyboard',expected) in self.window.commands.queue)
                if source:self.call(lambda:self.assertEqual(self.window.editor.get('1.0','end-1c'),'Preserved text'))
                if widget==self.window.typed:self.call(lambda:self.assertEqual(self.window.typed.get(),'Preserved header'))
                while not self.window.commands.empty():self.window.commands.get_nowait()
    def test_update_and_email_confirmation_override_nonempty_header(self):
        for mode in ('update_offer','email_draft'):
            self.window.set_context(dict(mode=mode,source=None,confirmation='yes',text='',caret=0,ack=0))
            self.wait(lambda:self.window.context.get('mode')==mode)
            def prepare():
                self.window.typed.delete(0,'end');self.window.typed.insert(0,'Existing header text');self.window.typed.focus_force()
            self.call(prepare)
            for key,expected in (('Tab','Tab'),('Return','Enter'),('y','y'),('n','n'),('Escape','Escape')):
                self.call(lambda key=key:self.window.typed.event_generate('<KeyPress>',keysym=key))
                self.wait(lambda expected=expected:('keyboard',expected) in self.window.commands.queue)
            while not self.window.commands.empty():self.window.commands.get_nowait()
    def test_html_links_tab_enter_and_alt_s(self):
        from email_reader import render_html
        body=render_html('<p>Hello <a href="https://example.com">Visit</a> and <a href="https://example.org">More</a></p>')
        self.window.set_context(dict(mode='mailbox',source='html-test',text=body,rich_spans=body.spans,caret=0,readonly=True,ack=0))
        self.wait(lambda:self.window.context.get('source')=='html-test')
        self.call(lambda:self.window.editor.focus_force())
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Tab'))
        self.call(lambda:self.assertEqual(self.window.editor.get('sel.first','sel.last'),'Visit'))
        with patch('webbrowser.open') as browser:
            self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Return'))
            browser.assert_called_once_with('https://example.com')
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Tab'))
        self.call(lambda:self.assertEqual(self.window.editor.get('sel.first','sel.last'),'More'))
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Tab',state=1))
        self.call(lambda:self.assertEqual(self.window.editor.get('sel.first','sel.last'),'Visit'))
        self.window.set_context(dict(mode='email_draft',source='draft-send',text='Hello',caret=0,readonly=False,ack=0))
        self.wait(lambda:self.window.context.get('mode')=='email_draft')
        self.call(lambda:self.window.editor.focus_force())
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='s',state=8))
        self.wait(lambda:('keyboard','Alt+S') in self.window.commands.queue)
    def test_reading_keys_speak_and_cursor_acknowledgment_preserves_feedback(self):
        output=Mock(problem=None);self.window.keyboard_output=output
        for mode in ('document','mailbox','help'):
            self.window.set_context(dict(mode=mode,source='reading-'+mode,text='First line.\nSecond line.\nThird line.',caret=0,selection=None,echo='characters',phonetic=False,delay=.5,ack=0,readonly=mode!='document'))
            self.wait(lambda:self.window.context.get('source')=='reading-'+mode)
            self.call(lambda:self.window.editor.focus_force())
            output.speak.reset_mock()
            self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Down'))
            self.wait(lambda:output.speak.called)
            output.speak.assert_called_with('Second line.')
            self.wait(lambda:not self.window.commands.empty())
            with patch.object(companion,'APP_WINDOW',self.window),patch.object(companion,'apply_keyboard_edit') as ack,patch.object(companion,'interrupt_speech') as stop,patch.object(companion,'KEYBOARD_BATCH_TEXT',None):
                companion.process_keyboard_batch(mode);self.assertTrue(ack.called);stop.assert_not_called()
            for keysym,control in (('Up',False),('Right',False),('Left',False),('Right',True),('Left',True),('Down',True),('Up',True),('Home',False),('End',False),('Home',True),('End',True)):
                output.speak.reset_mock()
                self.call(lambda key=keysym,ctrl=control:self.window.editor.event_generate('<KeyPress>',keysym=key,state=4 if ctrl else 0))
                self.wait(lambda:output.speak.called)
            while not self.window.commands.empty():self.window.commands.get_nowait()
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
        for event,command in (('<Escape>','Escape'),('<Alt-o>','Alt+O')):
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
    def test_typed_no_is_submitted_instead_of_default_yes(self):
        self.window.set_context(dict(mode='document_save',source=None,text='',confirmation='yes',echo='characters',phonetic=False,delay=.5,ack=0,focus=True))
        self.wait(lambda:self.window.context.get('confirmation')=='yes')
        self.call(lambda:self.window.typed.focus_force())
        self.call(lambda:(self.window.typed.insert(0,'no'),self.window.typed.event_generate('<Return>',when='tail')))
        self.wait(lambda:not self.window.commands.empty())
        self.assertEqual(self.window.commands.get(),'no')
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
        self.wait(lambda:not self.window.commands.empty());self.assertEqual(self.window.commands.get(),'name document Edited report')
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

    def test_media_editable_form_tab_commits_exact_text_and_combo_keys_do_not_type(self):
        base=dict(mode='media',source=None,text='',echo='characters',phonetic=False,delay=.5,ack=0,focus=True,media_navigation=True)
        self.window.set_context(dict(base,media_revision=1,media_input=True,media_form=dict(label='Time',value='8 PM',choices=())))
        self.call(lambda:self.window.typed.focus_force())
        self.call(lambda:(self.window.typed.delete(0,'end'),self.window.typed.insert(0,'9:30 PM'),self.window.typed.event_generate('<KeyPress>',keysym='Tab')))
        self.wait(lambda:not self.window.commands.empty());self.assertEqual(self.window.commands.get(),('media_field',{'value':'9:30 PM','next':'Tab'}))
        self.window.set_context(dict(base,menu_letters=True,media_revision=2,media_input=False,media_form=dict(label='Repeat every day',value='no',choices=('no','yes'))))
        self.call(lambda:self.window.typed.focus_force())
        for key,result in [('y','Letter:y'),('Down','Down'),('Tab','Tab')]:
            self.call(lambda k=key:self.window.typed.event_generate('<KeyPress>',keysym=k));self.wait(lambda:not self.window.commands.empty());self.assertEqual(self.window.commands.get(),('keyboard',result))
        values=[];self.call(lambda:values.append(self.window.typed.get()));self.assertEqual(values,[''])

    def test_alt_f4_requests_app_exit_even_from_edit_and_confirmation(self):
        for mode in ('web','media','exit_confirm'):
            self.window.set_context(dict(mode=mode,source=None,text='',echo='characters',phonetic=False,delay=.5,ack=0,focus=True,confirmation='no' if mode=='exit_confirm' else None))
            self.call(lambda:self.window.typed.focus_force())
            self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='F4',state=8))
            self.wait(lambda:not self.window.commands.empty());self.assertEqual(self.window.commands.get(),('keyboard','Alt+F4'))

    def test_f5_reloads_from_web_links_and_headings_lists(self):
        self.window.set_context(dict(mode='web',source=None,text='',menu_letters=True,web_name_list=True,echo='characters',phonetic=False,ack=0,focus=True))
        self.call(lambda:self.window.typed.focus_force())
        self.call(lambda:self.window.typed.event_generate('<KeyPress>',keysym='F5'))
        self.wait(lambda:not self.window.commands.empty())
        self.assertEqual(self.window.commands.get(),('keyboard','Web:Refresh'))

    def test_virtual_browser_reading_keys_dispatch_without_using_old_native_caret(self):
        context=dict(mode='web',source='page',text='Title\nBefore\nLater heading\nAfter',caret=0,readonly=True,web_browse=True,web_cursor_revision=1,echo='characters',phonetic=False,ack=0,focus=True)
        self.window.set_context(context);self.call(lambda:self.window.editor.focus_force())
        # Create an unacknowledged selection update to reproduce the old seq gate.
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='a',state=4))
        self.wait(lambda:not self.window.commands.empty());self.window.commands.get()
        target=context['text'].index('Later heading')
        self.window.set_context(dict(context,caret=target,web_cursor_revision=2,focus=False))
        self.call(lambda:self.assertEqual(len(self.window.editor.get('1.0','insert')),target))
        for key,state,expected in [('Down',0,'Web:Read:Down'),('Up',0,'Web:Read:Up'),('Right',4,'Web:Read:Control:Right'),('Home',0,'Web:Read:Home'),('Next',0,'Web:Read:Next')]:
            self.call(lambda k=key,s=state:self.window.editor.event_generate('<KeyPress>',keysym=k,state=s))
            self.wait(lambda:not self.window.commands.empty());self.assertEqual(self.window.commands.get(),('keyboard',expected))
        self.call(lambda:self.assertEqual(len(self.window.editor.get('1.0','insert')),target))

if __name__=='__main__':unittest.main()


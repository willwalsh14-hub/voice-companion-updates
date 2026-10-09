import copy
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
import companion
from app_window import CompanionWindow
from document_editor import VoiceDocument,Paragraph,TextRun
from keyboard_text import document_text,replace_keyboard_text,caret_feedback,typing_feedback,phonetic
from settings_model import DEFAULTS,CATEGORIES,fields

class KeyboardTextTests(unittest.TestCase):
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
    def test_all_typing_echo_options_and_replacement(self):
        self.assertEqual(typing_feedback('tes','test',4,'characters'),'t')
        self.assertIsNone(typing_feedback('tes','test',4,'words'))
        self.assertEqual(typing_feedback('test','test ',5,'words'),'test')
        self.assertEqual(typing_feedback('test','test ',5,'characters and words'),'space. test')
        self.assertIsNone(typing_feedback('tes','test',4,'none'))
        self.assertEqual(typing_feedback('cat','bat',1,'characters'),'b')
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
    def test_untitled_exit_prompts_and_name_is_not_document_text(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('Body stays separate.')]
            with patch.object(companion,'document',doc),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'KEYBOARD_DIRTY',{}),patch.object(companion,'speak'),patch.object(companion,'INPUT_MODE','mixed'):
                self.assertEqual(companion.handle('leave document','document'),'document_name')
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
    def test_readonly_message_moves_cursor_but_does_not_accept_typing(self):
        self.context('mailbox',True)
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='Right'))
        self.wait(lambda:not self.window.key_feedback.empty());self.assertEqual(self.window.key_feedback.get(),'b')
        self.call(lambda:self.window.editor.event_generate('<KeyPress>',keysym='z'))
        values=[];self.call(lambda:values.append(self.window.editor.get('1.0','end-1c')))
        self.assertEqual(values,['abc def'])

if __name__=='__main__':unittest.main()

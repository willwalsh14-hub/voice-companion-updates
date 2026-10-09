import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from field_selection import FieldSelection
from email_draft import VoiceEmail
from document_editor import Paragraph
from web_assistant import BrowserBackend, WebSession
from app_window import CompanionWindow

class CrossAreaSelectionTests(unittest.TestCase):
    def test_native_offsets_and_caret(self):
        selector=FieldSelection();text='First line.\nNext line.\n\nSecond paragraph.'
        _,span=selector.select(text,'select paragraph two',0,0)
        self.assertEqual(text[slice(*span)],'Second paragraph.')
        _,span=selector.select(text,'select previous paragraph',*span)
        self.assertEqual(text[slice(*span)],'First line.\nNext line.')
        _,span=selector.select(text,'select next line',0,0)
        self.assertEqual(text[slice(*span)],'Next line.')
        _,span=selector.select('abc','select current character',3,3)
        self.assertEqual(span,(2,3))
        self.assertIsNone(selector.select('abc','clear selection',*span)[1])
        self.assertIn('empty',selector.select('','select all')[0])

    def test_email_fields_preserve_values(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),recipient='test@example.com',subject='Two words')
            draft.cc=['copy@example.com'];draft.bcc=['blind@example.com']
            for field in ('recipient','subject','cc','bcc'):
                draft.compose_step=field
                before=(draft.recipient,draft.subject,draft.cc[:],draft.bcc[:])
                self.assertIn('Selected all',draft.process('select all'))
                self.assertIn('Selected:',draft.process('select first character'))
                self.assertEqual(before,(draft.recipient,draft.subject,draft.cc,draft.bcc))

    def test_every_input_mode_and_area(self):
        import companion
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),compose_step='body');draft.paragraphs=[Paragraph('Two words.')]
            for input_mode in ('mixed','commands','dictation'):
                with patch.object(companion,'INPUT_MODE',input_mode),patch.object(companion,'email_draft',draft),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'speak'):
                    companion.handle('select first word','email_draft')
                    self.assertEqual(draft.selected_text(),'Two');self.assertEqual(draft.paragraphs[0].text,'Two words.')
                with patch.object(companion,'INPUT_MODE',input_mode),patch.object(companion,'note_buffer',['Two words.']),patch.object(companion,'note_selection',FieldSelection()),patch.object(companion,'speak') as speech:
                    companion.handle('select all','note');self.assertIn('Selected all',speech.call_args.args[0]);self.assertEqual(companion.note_buffer,['Two words.'])
                web=Mock()
                with patch.object(companion,'INPUT_MODE',input_mode),patch.object(companion,'web_session',web),patch.object(companion,'speak'):
                    companion.handle('select next word','web');web.select_focused.assert_called_once_with('select next word');web.dictate_to_focus.assert_not_called()

    def test_window_queue_and_no_field_reload(self):
        window=CompanionWindow('test')
        def ui():
            action,request=window.ui_actions.get(timeout=1)
            self.assertEqual(action,'email_select');request['result']='Selected: typed but not submitted';request['done'].set()
        worker=threading.Thread(target=ui);worker.start()
        self.assertIn('typed but not submitted',window.select_email_text('select all'));worker.join()
        window.closed.set();self.assertIn('closed',window.select_email_text('select all'))
        import companion
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),compose_step='cc');window=Mock()
            with patch.object(companion,'email_draft',draft),patch.object(companion,'APP_WINDOW',window),patch.object(companion,'speak'):
                companion.handle('select all','email_draft');window.select_email_text.assert_called_once_with('select all');window.focus_email_field.assert_not_called()

    def test_browser_offsets_changes_and_password_feedback(self):
        class Frame:
            text='A😀 B';span=(0,0);changed=False;private=False
            def evaluate(self,script,data=None):
                if data is None:return {'key':'field','text':self.text,'start':self.span[0],'end':self.span[1],'private':self.private,'email':False}
                if self.changed:return 'changed'
                self.span=data['span'] or (0,0);return 'ok'
        frame=Frame();backend=BrowserBackend(Path('/unused'));backend.start=lambda:None;backend.page=SimpleNamespace(frames=[frame],keyboard=Mock())
        self.assertIn('Selected: 😀',backend.select_focused('select character two'));self.assertEqual(frame.span,[1,3])
        self.assertIn('Selected: B',backend.select_focused('select next word'));self.assertEqual(frame.span,[4,5])
        frame.changed=True;self.assertIn('changed',backend.select_focused('select all'))
        frame.changed=False;frame.private=True;self.assertNotIn('A😀 B',backend.select_focused('select all'))
        backend.page.frames=[];self.assertIn('No editable',backend.select_focused('select all'))

    def test_web_form_answer_preempted(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();backend.select_focused.return_value='Selected: field text'
            web=WebSession(folder,backend);web.snapshot={'url':'https://example.com'};web.form_index=0
            self.assertEqual(web.command('select all'),'Selected: field text');backend.fill.assert_not_called()

    def test_character_echo_when_typing_replaces_selection(self):
        from app_window import entry_feedback
        self.assertEqual(entry_feedback('Old address','a',0,1,'a','a'),'a')
        self.assertEqual(entry_feedback('a','a',0,1,'a','a'),'a')
        self.assertEqual(entry_feedback('address','@',0,1,'at','@'),'at sign')

import queue,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import companion as app
from reading_navigation import ReadingCursor,reading_request
from document_editor import VoiceDocument,Paragraph
from keyboard_text import replace_keyboard_text
class ReadingResponsivenessTests(unittest.TestCase):
    def window(self,commands):
        window=Mock();window.commands=queue.Queue()
        for command in commands:window.commands.put(command)
        return window
    def test_caret_acknowledgment_does_not_stop_readback(self):
        window=self.window([('text_edit',{'readonly':True})])
        with patch.object(app,'APP_WINDOW',window),patch.object(app,'apply_keyboard_edit') as edit,patch.object(app,'interrupt_speech') as stop,patch.object(app,'KEYBOARD_BATCH_TEXT',None):
            self.assertEqual(app.process_keyboard_batch('help'),'help');edit.assert_called_once();stop.assert_not_called();window.feedback.assert_not_called()
    def test_rapid_arrows_apply_all_moves_but_speak_final_item_only(self):
        window=self.window([('keyboard','Down')]*6)
        with patch.object(app,'APP_WINDOW',window),patch.object(app,'MAIN_MENU_INDEX',None),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'account_setup',False),patch.object(app,'interrupt_speech'),patch.object(app,'sync_app_context'),patch.object(app,'KEYBOARD_BATCH_TEXT',None):
            self.assertEqual(app.process_keyboard_batch('awake'),'awake');self.assertEqual(app.MAIN_MENU_INDEX,5);window.feedback.assert_called_once_with('Notes (N), 6 of 13.')
    def test_voice_navigation_fast_in_every_reading_context(self):
        with patch.object(app,'INPUT_MODE','mixed'):
            for mode in ('document','email_draft','mailbox','help','web','note'):
                for unit in ('character','word','line','sentence','paragraph'):
                    for direction in ('next','previous'):self.assertTrue(app.fast_command_request(direction+' '+unit,mode))
        self.assertEqual(reading_request('move to the previous word'),('word',-1));self.assertEqual(reading_request('read current paragraph'),('paragraph',0))
        with patch.object(app,'INPUT_MODE','dictation'):self.assertFalse(app.fast_command_request('next word','email_draft'))
    def test_spoken_navigation_uses_local_feedback_instead_of_online_generation(self):
        local=Mock(problem=None);ai=Mock(enabled=True)
        with patch.object(app,'KEYBOARD_SPEECH',local),patch.object(app,'AI_SPEECH',ai),patch.object(app,'TEXT_MODE',False),patch.object(app,'APP_WINDOW',None),patch.object(app,'_handle',side_effect=lambda *args:(app.speak('Second word.'),'help')[1]),patch.object(app,'sync_app_context'),patch.object(app,'update_audio_ducking'):
            app.handle('next word','help');local.speak.assert_called_once_with('Second word.');ai.speak.assert_not_called()
    def test_units_read_content_without_counters_and_cache_spans(self):
        cursor=ReadingCursor();cursor.set_text('One two.\nThree four.\nLast line.')
        for unit in ('character','word','line','sentence','paragraph'):
            cursor.position=0;value=cursor.move(unit,1);self.assertNotRegex(value,r'^(Character|Word|Line|Sentence|Paragraph) \d+ of ')
            spans=cursor._spans(unit);cursor.move(unit,-1);self.assertIs(cursor._spans(unit),spans)
        cursor.set_text('a b\nc');cursor.position=0;self.assertEqual(cursor.move('character',1),'space');cursor.position=2;self.assertEqual(cursor.move('character',1),'new line')
        cursor.position=1;self.assertEqual(cursor.move('word',-1),'a');cursor.position=1;self.assertEqual(cursor.move('word',1),'b')
    def test_keyboard_and_voice_share_position_and_cross_paragraphs(self):
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.paragraphs=[Paragraph('One two.'),Paragraph('Three four.')]
            text='One two.\nThree four.';replace_keyboard_text(doc,text,text,4)
            self.assertEqual(doc.process('next word'),'Three');self.assertEqual(doc.cursor,1)
            self.assertEqual(doc.process('previous word'),'two.');self.assertEqual(doc.cursor,0)
            self.assertEqual(doc.process('next paragraph'),'Three four.');self.assertEqual(doc.process('read current paragraph'),'Three four.')
    def test_control_paragraph_movement_skips_blank_separators(self):
        from keyboard_text import paragraph_destination
        text='One two.\n\nThree four.'
        self.assertEqual(paragraph_destination(text,0,1),10)
        self.assertEqual(paragraph_destination(text,15,-1),10)
        self.assertEqual(paragraph_destination(text,10,-1),0)
if __name__=='__main__':unittest.main()

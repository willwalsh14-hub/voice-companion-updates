import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dictation_echo import DictationEcho
from document_editor import VoiceDocument
from email_draft import VoiceEmail
from keyboard_text import document_text

class DictationRecoveryTests(unittest.TestCase):
    def test_delayed_phrase_sentence_and_off(self):
        now=[0];echo=DictationEcho(lambda:now[0])
        echo.add('Hello.', 'phrase by phrase')
        self.assertIsNone(echo.take());now[0]=.36
        self.assertIsNone(echo.take(busy=True));self.assertEqual(echo.take(),'Hello.')
        echo.add('I will','at the end of a sentence');now[0]=1
        self.assertIsNone(echo.take())
        echo.add('call. Next','at the end of a sentence');now[0]=2
        self.assertEqual(echo.take(),'I will call.')
        echo.add('day.','at the end of a sentence');now[0]=3
        self.assertEqual(echo.take(),'Next day.')
        echo.add('Secret.','do not echo dictated text');now[0]=4
        self.assertIsNone(echo.take())
        echo.add('Old.','phrase by phrase');echo.clear();now[0]=5
        self.assertIsNone(echo.take())

    def test_real_spoken_phrases_survive_interrupt_until_sentence_ends(self):
        import companion as c
        from queue import Queue
        from types import SimpleNamespace
        echo=DictationEcho(lambda:0)
        with tempfile.TemporaryDirectory() as folder:
            doc=VoiceDocument(Path(folder));doc.dictating=True
            window=SimpleNamespace(commands=Queue(),ui_actions=Queue(),show=lambda text:None)
            with patch.object(c,'APP_WINDOW',window),patch.object(c,'document',doc),patch.object(c,'DICTATION_ECHO',echo),patch.object(c,'TEXT_MODE',False),patch.object(c,'speak'),patch.object(c,'sync_app_context'),patch.object(c,'INPUT_MODE','dictation'),patch.object(c,'KEYBOARD_SPEECH',None),patch.object(c,'ESPEAK_SPEECH',None),patch.object(c,'AI_SPEECH',None),patch.dict(c.PREFERENCES,{'dictation_echo':'at the end of a sentence'}):
                for phrase in ('I will call','you tomorrow','period'):
                    c.handle(phrase,'document')
                self.assertEqual(list(echo.pending),['I will call you tomorrow.'])
                c.interrupt_speech()
                self.assertEqual(echo.sentence,'')
                self.assertFalse(echo.pending)

    def test_real_dictation_type_and_keyboard_recovery_in_document_and_mail(self):
        import companion as c
        with tempfile.TemporaryDirectory() as folder:
            for kind,mode in ((VoiceDocument,'document'),(VoiceEmail,'email_draft')):
                for choice,expected in (('old school','I I, um, will call.'),('ai-based','I will call.')):
                    editor=kind(Path(folder)/mode);editor.dictating=True
                    if mode=='email_draft':editor.compose_step='body'
                    with patch.object(c,'document',editor),patch.object(c,'email_draft',editor),patch.object(c,'APP_WINDOW',None),patch.object(c,'INPUT_MODE','dictation'),patch.object(c,'EDIT_ACK',{}),patch.object(c,'KEYBOARD_DIRTY',{}),patch.object(c,'sync_app_context'),patch.object(c,'speak'),patch.object(c,'focus_email_entry'),patch.dict(c.PREFERENCES,{'dictation_type':choice}):
                        c.handle('I I, um, will call.',mode)
                        self.assertEqual(document_text(editor),expected)
                        source=c.app_context(mode)['source']
                        self.assertFalse(c.apply_keyboard_edit(dict(source=source,before='',after='',caret=0,seq=1),mode))
                        self.assertEqual(c.EDIT_ACK[source],1)
                        self.assertEqual(document_text(editor),expected)
                        self.assertTrue(c.apply_keyboard_edit(dict(source=source,before=expected,after=expected,caret=0,seq=2),mode))
                        self.assertTrue(c.apply_keyboard_edit(dict(source=source,before=expected,after='X'+expected,caret=1,seq=3),mode))
                        self.assertEqual(document_text(editor),'X'+expected)

    def test_echo_setting_voice_save_and_reload_without_engine_changes(self):
        import json
        import companion as c
        from settings_model import DEFAULTS, SettingsSession
        with tempfile.TemporaryDirectory() as folder,patch.object(c,'APP',Path(folder)),patch.object(c,'APP_WINDOW',None),patch.object(c,'PREFERENCES',dict(DEFAULTS)),patch.object(c,'speak'),patch.object(c,'apply_settings',side_effect=AssertionError('Do not change engines')):
            for choice in ('phrase by phrase','at the end of a sentence','do not echo dictated text'):
                c.handle('echo dictated text '+choice,'awake')
                saved=json.loads((Path(folder)/'preferences.json').read_text())
                self.assertEqual(SettingsSession(saved,{}).values['dictation_echo'],choice)

    def test_echo_has_no_added_prefix(self):
        import companion as c
        echo=DictationEcho(lambda:0)
        with patch.object(c,'TEXT_MODE',False),patch.object(c,'APP_WINDOW',None),patch.object(c,'DICTATION_ECHO',echo),patch.dict(c.PREFERENCES,{'dictation_echo':'phrase by phrase'}):
            c.dictation_feedback('Added: Hello world.')
            self.assertEqual(list(echo.pending),['Hello world.'])

if __name__=='__main__':unittest.main()

import queue,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import companion as app
from email_headers import HeaderEditor
from settings_model import SettingsSession,CATEGORIES
from keyboard_speech import KeyboardSpeech
from mail_voice import MailSession,sender_address,sender_name
from onboard_help import HelpSession
from speech_controls import request
from voice_pitch import sapi_speak
class AccessibilityFixTests(unittest.TestCase):
    def test_latest_keyboard_announcement_replaces_backlog(self):
        speech=KeyboardSpeech.__new__(KeyboardSpeech);speech.queue=queue.Queue();speech.narration_interrupt=threading.Event()
        speech.queue.put('old selection');speech.speak('latest selection')
        self.assertEqual(list(speech.queue.queue),[('interrupt',),'latest selection']);self.assertTrue(speech.narration_interrupt.is_set())
        speech.narration_interrupt.clear();speech.interrupt(notify=False);self.assertFalse(speech.narration_interrupt.is_set())
    def test_keyboard_interrupts_before_selection_and_home_end(self):
        events=[]
        with patch.object(app,'interrupt_speech',side_effect=lambda:events.append('stop')),patch.object(app,'speak',side_effect=lambda s:events.append(s)),patch.object(app,'account_setup',False),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'MAIN_MENU_INDEX',None):
            app.navigation_key('Letter:e','awake');self.assertEqual(events,['stop','Email (E), 2 of 13.'])
            app.navigation_key('End','awake');self.assertEqual(app.MAIN_MENU_INDEX,12)
            app.navigation_key('Home','awake');self.assertEqual(app.MAIN_MENU_INDEX,0)
    def test_confirmation_y_n_and_control(self):
        with patch.object(app,'interrupt_speech') as stop:
            for mode in ('exit_confirm','power_confirm'):
                self.assertEqual(app.navigation_key('y',mode),'yes');self.assertEqual(app.navigation_key('N',mode),'no')
            self.assertIsNone(app.navigation_key('Control','awake'));self.assertEqual(stop.call_count,5)
    def test_message_home_end_and_no_addresses(self):
        session=MailSession.__new__(MailSession);session.rows=[{'from':'Sam <sam@example.com>','subject':'One'},{'from':'other@example.com','subject':'Two'}]
        session.view='folder';session.folder_picker=None;session.pending=None;session.current=1
        with patch.object(app,'mail_session',session),patch.object(app,'speak') as say,patch.object(app,'interrupt_speech'),patch.object(app,'account_setup',False):
            app.navigation_key('End','mailbox');self.assertEqual(session.current,2);self.assertNotIn('@',say.call_args.args[0])
            app.navigation_key('Home','mailbox');self.assertEqual(session.current,1);self.assertIn('Sam.',say.call_args.args[0])
        self.assertEqual(sender_address(session.rows[0]),'sam@example.com');self.assertEqual(sender_name({'sender_name':'Dana','from':'dana@example.com'}),'Dana')
    def test_help_reading_context_and_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            guide=Path(folder,'guide.txt');guide.write_text('Guide\nEXAMPLE\nFirst paragraph.\nSecond paragraph.')
            help=HelpSession(guide);help.process('ok')
            with patch.object(app,'help_session',help):
                context=app.app_context('help');self.assertTrue(context['readonly']);self.assertIn('First paragraph.',context['text'])
                payload={'source':context['source'],'after':context['text'],'caret':3,'seq':1,'readonly':True}
                with patch.object(app,'sync_app_context'):self.assertTrue(app.apply_keyboard_edit(payload,'help'))
                self.assertEqual(help.reading.position,3)
            help.process('go back');self.assertEqual(help.level,'topics');help.process('go back');self.assertTrue(help.closed)
    def test_header_toggle_order_cancel_and_last_enabled(self):
        session=SettingsSession({},{});model=HeaderEditor(session);model.index=3;model.toggle();model.move(-1)
        self.assertEqual(session.values['email_header_order'],'from, subject, size, date')
        model.index=0;model.toggle();self.assertEqual(session.values['email_header_order'],'subject, size, date')
        session.cancel();self.assertEqual(session.values['email_header_order'],'from, subject, date')
        session=SettingsSession({'email_header_order':'from'},{});model=HeaderEditor(session)
        with self.assertRaises(ValueError):model.toggle()
    def test_pitch_parser_settings_and_xml_escaping(self):
        for phrase,expected in [('higher pitch',('pitch','delta',1)),('pitch down',('pitch','delta',-1)),('set voice pitch minus three',('pitch','set',-3))]:self.assertEqual(request(phrase),expected)
        session=SettingsSession({},{});self.assertIn('Voice',CATEGORIES);self.assertNotIn('Speech',CATEGORIES);self.assertEqual(session.voice_setting('pitch up'),('pitch','1'))
        voice=Mock();sapi_speak(voice,'<hello> & goodbye',4);voice.Speak.assert_called_once_with('<pitch absmiddle="4">&lt;hello&gt; &amp; goodbye</pitch>',11)
    def test_keyboard_feedback_uses_local_engine_even_with_ai(self):
        local=Mock(problem=None);ai=Mock(enabled=True)
        with patch.object(app,'TEXT_MODE',False),patch.object(app,'APP_WINDOW',None),patch.object(app,'KEYBOARD_NAVIGATION',True),patch.object(app,'KEYBOARD_SPEECH',local),patch.object(app,'AI_SPEECH',ai),patch.object(app,'update_audio_ducking'):
            app.speak('Email (E).');local.speak.assert_called_once();ai.speak.assert_not_called()
    def test_sender_actions_copy_exact_address_and_start_fresh_draft(self):
        from types import SimpleNamespace
        session=SimpleNamespace(rows=[{'from':'Sam <sam@example.com>'}],current=1,pending=None)
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'APP',Path(folder)),patch.object(app,'mail_session',session),patch.object(app,'email_draft',None),patch.object(app,'document',None),patch.object(app,'APP_WINDOW',None),patch.object(app,'SETTINGS_PANEL',None),patch.object(app,'account_setup',False),patch.object(app,'speak') as say,patch.object(app,'ProtectedStore') as store,patch('document_editor.copy_to_clipboard') as copy,patch('document_editor.CLIPBOARD_WRITE_OK',True):
            store.return_value.selected.return_value=('gmail','me@example.com')
            self.assertEqual(app._handle('copy sender address','mailbox'),'mailbox');copy.assert_called_once_with('sam@example.com');say.assert_called_with('Sender address copied.')
            self.assertEqual(app._handle('new email to sender','mailbox'),'email_draft')
            self.assertEqual(app.email_draft.recipient,'sam@example.com');self.assertEqual(app.email_draft.compose_step,'subject');self.assertFalse(app.email_draft.response_context)
            self.assertEqual(app.navigation_key('Ctrl+Shift+C','mailbox'),'copy sender address');self.assertEqual(app.navigation_key('Ctrl+Shift+N','mailbox'),'new email to sender')
if __name__=='__main__':unittest.main()

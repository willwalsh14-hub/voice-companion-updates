import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
import companion as app
from email_draft import VoiceEmail
from menu_navigation import next_match
from mail_voice import MailSession

class MenuKeyboardTests(unittest.TestCase):
    def test_repeated_letters_cycle_matching_main_items_without_activation(self):
        with patch.object(app,'MAIN_MENU_INDEX',None),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'account_setup',False),patch.object(app,'speak') as say:
            app.navigation_key('Letter:e','awake')
            self.assertEqual(app.MAIN_MENU_CHOICES[app.MAIN_MENU_INDEX][0],'Email')
            say.assert_called_with('Email (E), 2 of 13.')
            app.navigation_key('Letter:e','awake')
            self.assertEqual(app.MAIN_MENU_CHOICES[app.MAIN_MENU_INDEX][0],'Exit Voice Companion')
            app.navigation_key('Letter:e','awake')
            self.assertEqual(app.MAIN_MENU_CHOICES[app.MAIN_MENU_INDEX][0],'Email')
    def test_folder_letters_only_select_and_do_not_touch_messages(self):
        picker={'folders':[('Inbox','i'),('Sent','s'),('Stuff','x')],'index':0}
        session=SimpleNamespace(folder_picker=picker,pending=None,_spoken_folder=lambda x:x)
        with patch.object(app,'mail_session',session),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'account_setup',False),patch.object(app,'speak'):
            app.navigation_key('Letter:s','mailbox');self.assertEqual(picker['index'],1)
            app.navigation_key('Letter:s','mailbox');self.assertEqual(picker['index'],2)
            session.folder_picker=None
            self.assertIsNone(app.menu_state('mailbox'))
    def test_tab_preserves_headers_and_cycles_both_directions(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder));draft.compose_step='recipient'
            with patch.object(app,'email_draft',draft),patch.object(app,'pending_send',None),patch.object(app,'speak'):
                request=app.commit_email_field_tab({'field':'recipient','value':'a@example.com','backward':False},'email_draft')
                self.assertEqual((draft.recipient,request),('a@example.com','go to cc'))
                draft.process(request)
                request=app.commit_email_field_tab({'field':'cc','value':'b@example.com, c@example.com','backward':False},'email_draft')
                self.assertEqual(draft.cc,['b@example.com','c@example.com']);self.assertEqual(request,'go to bcc')
                draft.process(request)
                self.assertEqual(app.navigation_key('ShiftTab','email_draft'),'go to cc')
                draft.compose_step='body'
                self.assertEqual(app.navigation_key('Tab','email_draft'),'go to to')
                self.assertEqual(app.navigation_key('ShiftTab','email_draft'),'go to subject')
    def test_invalid_address_keeps_focus_and_previous_data(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder));draft.compose_step='recipient';draft.recipient='good@example.com'
            with patch.object(app,'email_draft',draft),patch.object(app,'speak'):
                self.assertIsNone(app.commit_email_field_tab({'field':'recipient','value':'bad address','backward':False},'email_draft'))
            self.assertEqual((draft.compose_step,draft.recipient),('recipient','good@example.com'))
    def test_reply_subject_can_be_visited_but_not_changed(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder));draft.subject='Re: Hello';draft.response_context={'action':'reply'}
            draft.process('go to subject')
            self.assertEqual(draft.compose_step,'subject')
            with patch.object(app,'email_draft',draft),patch.object(app,'pending_send',None),patch.object(app,'speak'):
                self.assertEqual(app.commit_email_field_tab({'field':'subject','value':'Re: Hello','backward':False},'email_draft'),'go to body')
                self.assertIsNone(app.commit_email_field_tab({'field':'subject','value':'Changed','backward':False},'email_draft'))
            self.assertEqual(draft.subject,'Re: Hello')
    def test_power_aliases_always_require_existing_confirmation(self):
        with patch.object(app,'document',None),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None),patch.object(app,'speak'),patch('windows_actions.request_power') as power:
            for phrase in ('shut off the computer','turn the computer off','shut the computer down','reboot the damn thing','restart the computer'):
                self.assertTrue(app.fast_command_request(phrase,'document'))
                self.assertEqual(app.handle(phrase,'awake'),'power_confirm')
            power.assert_not_called()
    def test_message_summary_has_no_header_labels_or_menu_key(self):
        session=MailSession.__new__(MailSession)
        session.rows=[{'from':'Sam','subject':'Hello','date':'Today'}]
        self.assertEqual(session._summary(1),'Sam. Hello. Today. 1 of 1.')
    def test_unmatched_letter_keeps_current_item(self):
        self.assertIsNone(next_match(['Email','Exit'],0,'z'))

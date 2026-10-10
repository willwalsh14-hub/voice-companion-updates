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

    def test_main_escape_confirms_and_second_escape_cancels(self):
        with patch.object(app,'PREFERENCES',{'ask_before_exit':True}),patch.object(app,'document',None),patch.object(app,'speak'),patch.object(app,'finish_exit') as finish:
            self.assertEqual(app.navigation_key('Escape','awake'),'exit companion')
            mode=app._handle('exit companion','awake')
            self.assertEqual(mode,'exit_confirm');self.assertEqual(app.CONFIRM_CHOICE,'no')
            self.assertEqual(app._handle(app.navigation_key('Escape',mode),mode),'awake')
            finish.assert_not_called()
            self.assertIsNone(app.navigation_key('Tab',mode));self.assertEqual(app.CONFIRM_CHOICE,'yes')
            app._handle(app.navigation_key('Enter',mode),mode);finish.assert_called_once_with('awake')
    def test_disabled_exit_and_power_confirmation(self):
        with patch.object(app,'PREFERENCES',{'ask_before_exit':False,'ask_before_restart':False,'ask_before_shutdown':False}),patch.object(app,'document',None),patch.object(app,'email_draft',None),patch.object(app,'speak'),patch.object(app,'finish_exit',return_value='exit') as finish,patch.object(app,'flush_note'),patch('windows_actions.request_power') as power:
            self.assertEqual(app._handle('exit companion','awake'),'exit');finish.assert_called_once()
            for phrase,action in [('restart computer','restart'),('shut down computer','shutdown')]:
                self.assertEqual(app._handle(phrase,'awake'),'exit');power.assert_called_with(action)
    def test_configurable_header_order_names_and_size(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            session=MailSession.__new__(MailSession);session.folder=folder
            session.rows=[{'from':'Sam','subject':'Hello','date':'Today','size':2048}]
            Path(folder,'preferences.json').write_text(json.dumps({'email_header_order':'subject, size, from','email_header_names':True}))
            self.assertEqual(session._summary(1),'Subject Hello. Size 2048 bytes. From Sam. 1 of 1.')
            Path(folder,'preferences.json').write_text(json.dumps({'email_header_order':'from','email_header_names':False}))
            self.assertEqual(session._summary(1),'Sam. 1 of 1.')
    def test_header_settings_validation_and_defaults(self):
        from settings_model import SettingsSession,DEFAULTS
        session=SettingsSession({}, {})
        self.assertTrue(all(DEFAULTS[k] for k in ('ask_before_exit','ask_before_restart','ask_before_shutdown')))
        self.assertFalse(DEFAULTS['email_header_names']);self.assertNotIn('size',DEFAULTS['email_header_order'])
        session.set('email_header_order','subject, from, size')
        for value in ('from, from','unknown',''):
            with self.assertRaises(ValueError):session.set('email_header_order',value)
        self.assertEqual(session.voice_setting('set ask before exiting Voice Companion off'),('ask_before_exit','off'))
        session.cancel();self.assertEqual(session.values['email_header_order'],'from, subject, date')
    def test_outlook_size_metadata(self):
        from mailbox_access import MailboxClient
        client=MailboxClient('outlook','test')
        with patch.object(client,'_call',return_value={'value':[{'id':'a','singleValueExtendedProperties':[{'id':'Integer 0x0E08','value':'4096'}]}]}) as call:
            rows,_=client.list_messages('inbox');self.assertEqual(rows[0]['size'],4096)
            self.assertIn('0x0E08',call.call_args.args[0])

import base64,unittest,tempfile,queue
from pathlib import Path
from email.message import EmailMessage
from unittest.mock import patch,Mock
from urllib.error import HTTPError
from email_reader import render_html,plain_body,safe_link
from mailbox_access import gmail_body
from email_draft import VoiceEmail
from document_editor import Paragraph
import companion as app
from email_delivery import submit,DeliveryError
class EmailReaderTests(unittest.TestCase):
    def test_html_has_links_lists_and_styles_without_source_whitespace(self):
        body=render_html('<html><head><style>hidden</style></head><body>\n <div><p>Hello <b>friend</b></p>\n <ul><li>First</li><li><a href="https://example.com">Visit</a></li></ul></div><script>evil</script></body></html>')
        self.assertEqual(body,'Hello friend\n• First\n• Visit')
        self.assertTrue(any(body[a:b]=='friend' and s=='bold' for a,b,s,u in body.spans))
        self.assertTrue(any(body[a:b]=='Visit' and u=='https://example.com' for a,b,s,u in body.spans))
    def test_plain_keeps_one_blank_line(self):
        self.assertEqual(plain_body('\r\nOne\r\n \r\n\r\n\r\nTwo\r\n'),'One\n\nTwo')
    def test_mime_prefers_html_and_preserves_link(self):
        msg=EmailMessage();msg.set_content('plain');msg.add_alternative('<p><a href="https://example.com">HTML</a></p>',subtype='html')
        body=gmail_body(base64.urlsafe_b64encode(msg.as_bytes()).decode())
        self.assertEqual(body,'HTML');self.assertEqual(body.spans[0][3],'https://example.com')
    def test_links_only_allow_expected_schemes(self):
        for url in ('javascript:alert(1)','file:///c:/secret','data:text/html,hi',''):self.assertFalse(safe_link(url))
        self.assertEqual(render_html('<a href="javascript:alert(1)">Unsafe</a>').spans,[])
    def test_default_and_voice_format_setting(self):
        from settings_model import DEFAULTS,SettingsSession
        self.assertEqual(DEFAULTS['email_format'],'html')
        session=SettingsSession(DEFAULTS,{})
        request=session.voice_setting('email format plain text');session.set(*request)
        self.assertEqual(session.values['email_format'],'plain text')
    def test_update_and_send_confirmation_keys(self):
        for mode in ('update_offer','email_draft'):
            with patch.object(app,'pending_send',('hash','gmail','address')),patch.object(app,'CONFIRM_CHOICE','yes'),patch.object(app,'speak'),patch.object(app,'interrupt_speech'):
                self.assertEqual(app.navigation_key('y',mode),'yes')
                self.assertEqual(app.navigation_key('n',mode),'no')
                self.assertIsNone(app.navigation_key('Tab',mode));self.assertEqual(app.CONFIRM_CHOICE,'no')
                self.assertEqual(app.navigation_key('Enter',mode),'no')
                self.assertEqual(app.navigation_key('Escape',mode),'no')
                self.assertIsNone(app.navigation_key('ShiftTab',mode));self.assertEqual(app.navigation_key('Enter',mode),'yes')
                self.assertEqual(app.app_context(mode)['confirmation'],'yes')
    def test_alt_s_uses_same_send_review(self):
        self.assertEqual(app.navigation_key('Alt+S','email_draft'),'send email')
    def test_send_commits_last_header_and_invalid_address_does_not_send(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder));draft.compose_step='recipient'
            with patch.object(app,'email_draft',draft),patch.object(app,'speak'):
                payload={'field':'recipient','value':'bad','backward':False,'send':True}
                self.assertIsNone(app.commit_email_field_tab(payload,'email_draft'))
                payload['value']='friend@example.com'
                self.assertEqual(app.commit_email_field_tab(payload,'email_draft'),'send email')
                self.assertEqual(draft.recipient,'friend@example.com')
    def test_expired_signin_is_not_recorded_as_an_attempt(self):
        from mail_accounts import AccountError
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),provider='gmail',recipient='friend@example.com',subject='Test');draft.paragraphs=[Paragraph(text='Hello')]
            with patch.object(app,'email_draft',draft),patch.object(app.ProtectedStore,'load',return_value={'address':'me@example.com'}),patch.object(app,'account_token',side_effect=AccountError('Gmail sign-in expired. Reconnect the account.')),patch.object(app,'speak') as speech:
                app.pending_send=None;app.handle('send it','email_draft');app.handle('yes','email_draft')
                self.assertEqual(draft.last_attempt_hash,'');self.assertIn('sign-in expired',speech.call_args.args[0]);self.assertIn('It has not been sent',speech.call_args.args[0])
    def test_definite_rejection_releases_attempt_block(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),provider='gmail',recipient='friend@example.com',subject='Test');draft.paragraphs=[Paragraph(text='Hello')]
            with patch.object(app,'email_draft',draft),patch.object(app.ProtectedStore,'load',return_value={'address':'me@example.com'}),patch.object(app,'account_token',return_value=('me@example.com','token')),patch.object(app,'submit',side_effect=DeliveryError('Permission denied',uncertain=False)) as sender,patch.object(app,'speak') as speech:
                app.pending_send=None;app.handle('send it','email_draft');app.handle('yes','email_draft')
                self.assertEqual(draft.last_attempt_hash,'')
                app.handle('send it','email_draft');self.assertIn('Review before sending',speech.call_args.args[0]);sender.assert_called_once()
                app.pending_send=None
    def test_format_switch_uses_original_plain_part(self):
        from mail_voice import MailSession
        body=render_html('<p>HTML</p>');body.plain='Original plain'
        session=MailSession('gmail','unused');session.body_content=body
        session.set_format('html');self.assertEqual(session.body_text,'HTML')
        session.set_format('plain text');self.assertEqual(session.body_text,'Original plain')
        session.set_format('html');self.assertEqual(session.body_text.spans,body.spans)
    def test_permission_rejection_is_definite(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),provider='gmail',recipient='friend@example.com',subject='Test');draft.paragraphs=[Paragraph(text='Hello')]
            with self.assertRaises(DeliveryError) as caught:
                submit('gmail',draft,'token',opener=Mock(side_effect=HTTPError('url',403,'Forbidden',{},None)))
            self.assertFalse(caught.exception.uncertain);self.assertIn('permission',str(caught.exception))
if __name__=='__main__':unittest.main()

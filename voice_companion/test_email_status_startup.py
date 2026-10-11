import base64,json,queue,tempfile,unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock,patch
from email_draft import VoiceEmail
from document_editor import Paragraph
from email_delivery import gmail_request,microsoft_request,message_parts,DeliveryError
import companion as app
class EmailStatusStartupTests(unittest.TestCase):
    def draft(self,folder,subject,body):
        draft=VoiceEmail(Path(folder),provider='gmail',recipient='friend@example.com',subject=subject)
        draft.paragraphs=[Paragraph(text=body)] if body else []
        return draft
    def test_subject_only_and_body_only_provider_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            for subject,body in (('Only subject',''),('','Only body')):
                draft=self.draft(folder,subject,body)
                self.assertEqual(message_parts(draft),('friend@example.com',subject,body))
                msg=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(json.loads(gmail_request(draft,'token').data)['raw']))
                self.assertEqual(str(msg['Subject']),subject)
                payload=json.loads(microsoft_request(draft,'token').data)
                self.assertEqual(payload['message']['subject'],subject)
            with self.assertRaises(DeliveryError):message_parts(self.draft(folder,'',''))
    def test_skip_subject_preserves_guided_compose(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=self.draft(folder,'','');draft.compose_step='subject'
            self.assertIn('No subject',draft.process('skip subject'));self.assertEqual(draft.compose_step,'body')
            draft.process('This is the body.');self.assertIn('This is the body.',draft.paragraphs[0].text)
    def test_status_bypasses_coalescing_but_navigation_stays_coalesced(self):
        window=Mock();window.commands=queue.Queue();window.commands.put(('keyboard','Down'))
        spoken=[]
        def action(*args):
            app.mail_progress('Getting messages from Inbox. Please wait.')
            app.speak('Inbox summary.');return 'mailbox'
        with patch.object(app,'APP_WINDOW',window),patch.object(app,'TEXT_MODE',False),patch.object(app,'KEYBOARD_SPEECH',Mock(problem=None)),patch.object(app,'KEYBOARD_NAVIGATION',True),patch.object(app,'update_audio_ducking'),patch.object(app,'interrupt_speech'),patch.object(app,'navigation_key',return_value='next'),patch.object(app,'handle_keyboard',side_effect=action),patch.object(app,'finish_mail_announcement') as finish:
            app.process_keyboard_batch('mailbox')
            app.KEYBOARD_SPEECH.speak.assert_called_once_with('Getting messages from Inbox. Please wait.')
            window.feedback.assert_called_once_with('Inbox summary.');finish.assert_called_once()
    def test_keyboard_send_success_spoken_before_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=self.draft(folder,'Subject only','');session=Mock();session.list_messages.return_value='Inbox list';session.page_cursor=None;session.folder_name='Inbox'
            trace=[]
            def speech(text):trace.append(('speech',text,app.KEYBOARD_BATCH))
            session.list_messages.side_effect=lambda *args:trace.append(('refresh',)) or 'Inbox list'
            with patch.object(app,'email_draft',draft),patch.object(app,'mail_session',session),patch.object(app.ProtectedStore,'load',return_value={'address':'me@example.com'}),patch.object(app,'account_token',return_value=('me@example.com','token')),patch.object(app,'submit') as send,patch.object(app,'speak',side_effect=speech),patch.object(app,'finish_mail_announcement'),patch.object(app,'KEYBOARD_BATCH',True):
                app.pending_send=None;app.handle_keyboard('send it','email_draft');send.assert_not_called()
                self.assertIn('no message body',trace[-1][1])
                app.handle_keyboard('yes','email_draft');send.assert_called_once()
                self.assertLess(trace.index(('speech','Email sent successfully.',False)),trace.index(('refresh',)))
    def test_local_status_marks_active_when_speech_starts(self):
        import sys
        from keyboard_speech import KeyboardSpeech
        worker=KeyboardSpeech.__new__(KeyboardSpeech);worker.settings=('',0,100,None,0);worker.problem=None;worker.active=False;worker.quiet_until=0
        worker.queue=Mock(get=Mock(side_effect=[('interrupt',),'Email sent successfully.',None]))
        voice=Mock()
        def talking(*args):
            if args[0]:self.assertTrue(worker.active)
        voice.Speak.side_effect=talking
        with patch.dict(sys.modules,{'pythoncom':Mock(),'win32com':Mock(),'win32com.client':Mock(Dispatch=Mock(return_value=voice))}):worker.run()
    def test_wake_interrupts_existing_speech(self):
        with patch.object(app,'SLEEP_RETURN_MODE','awake'),patch.object(app,'interrupt_speech') as stop,patch.object(app,'announce_main_menu') as announce,patch.object(app,'sync_app_context'):
            self.assertEqual(app.resume_from_sleep(),'awake');stop.assert_called_once();announce.assert_called_once()
        self.assertNotIn('wake up',app.startup_prompt().lower())
if __name__=='__main__':unittest.main()

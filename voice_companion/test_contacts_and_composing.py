"""New guided mail, local contacts, provider BCC, and link choice checks."""
import base64
import json
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch, MagicMock

from address_book import AddressBook
from email_draft import VoiceEmail
from email_delivery import gmail_request, microsoft_request
from google_contacts import sync
from test_web_assistant import FakeBackend
from web_assistant import WebSession
from media_hub import MediaHub
from app_window import entry_feedback


class GuidedChecks(unittest.TestCase):
    def test_radio_phrases_and_separate_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub=MediaHub(tmp,fetch_json=lambda url: [{'name':'WGN Radio','url':'https://radio.example/wgn','country':'US'}])
            self.assertIn('WGN Radio',hub.command('search WGN',section='radio'))
            with patch.object(hub,'play_station',return_value='Playing WGN Radio') as play:
                self.assertIn('Playing',hub.command('play station number one',section='radio'))
                play.assert_called_once_with(1)
            self.assertNotIn('subscription',hub.command('nonsense',section='radio').lower())
            self.assertIn('podcasts',hub.command('nonsense',section='podcast').lower())

    def test_radio_number_and_result_browsing_end_to_end(self):
        import companion
        with tempfile.TemporaryDirectory() as tmp:
            hub=MediaHub(tmp,fetch_json=lambda url: [
                {'name':'First Radio','url':'https://radio.example/one','country':'US'},
                {'name':'Second Radio','url':'https://radio.example/two','country':'US'}])
            hub.search_radio('name','radio')
            with patch.object(hub,'play_station',side_effect=lambda number: 'Played '+str(number)) as play, \
                 patch.object(companion,'MEDIA_HUB',hub),patch.object(companion,'MEDIA_SECTION','radio'), \
                 patch.object(companion,'speak') as speech:
                for phrase,number in [('play station 1',1),('play station number 2',2),
                                      ('play 1',1),('play the first one',1),('play the second one',2),
                                      ('play station won',1),('play station too',2),('please play station 1',1)]:
                    self.assertEqual(companion.handle(phrase,'media'),'media')
                    self.assertEqual(play.call_args.args[0],number,phrase)
                    self.assertIn('Played',speech.call_args.args[0])
                hub.station_index=0
                companion.handle('next','media')
                self.assertEqual(hub.station_index,1)
                companion.handle('that one','media')
                self.assertEqual(play.call_args.args[0],2)
                companion.handle('go back','media')
                self.assertEqual(hub.station_index,0)
                companion.handle('confirm that','media')
                self.assertEqual(play.call_args.args[0],1)
                self.assertEqual(companion.handle('main menu','media'),'awake')

    def test_radio_name_call_letters_frequency_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub=MediaHub(tmp)
            hub.stations=[{'name':'WJLB 97.9 FM'}, {'name':'WJLB 105.9 HD'}, {'name':'WGN Radio'}]
            with patch.object(hub,'play_station',side_effect=lambda number: 'Played '+str(number)) as play:
                self.assertEqual(hub.command('play 97.9 WJLB',section='radio'),'Played 1')
                self.assertEqual(hub.command('play W J L B 97 point 9',section='radio'),'Played 1')
                self.assertEqual(hub.command('play WGN',section='radio'),'Played 3')
                count=play.call_count
                response=hub.command('play WJLB',section='radio')
                self.assertIn('2 matching stations',response)
                self.assertIn('97.9',response)
                self.assertEqual(play.call_count,count)
                self.assertIn('did not find',hub.command('play unknown',section='radio'))
                self.assertEqual(play.call_count,count)
                for phrase,expected in [('the first one',1),('1',1),('station 1',1),
                                        ('second one',2),('2',2),('station 2',2)]:
                    hub.command('play WJLB',section='radio')
                    self.assertEqual(hub.command(phrase,section='radio'),'Played '+str(expected),phrase)
                hub.command('play WJLB',section='radio')
                self.assertIn('only 2',hub.command('station 3',section='radio'))
                self.assertEqual(hub.command('2',section='radio'),'Played 2')

    def test_address_entry_feedback_and_subject_voice_navigation(self):
        self.assertEqual(entry_feedback('bob@example.com','bob@example.com',2,1,'Left'),'o')
        self.assertEqual(entry_feedback('bob@example.com','bob@example.com',2,3,'Right'),'at sign')
        self.assertEqual(entry_feedback('bob@example.com','bo@example.com',3,2,'BackSpace'),'Deleted b')
        self.assertEqual(entry_feedback('bob','bob@',3,4,'at'),'at sign')
        import companion
        with tempfile.TemporaryDirectory() as tmp:
            draft=VoiceEmail(Path(tmp)/'Email Drafts',recipient='wrong@example.com',subject='Hello',compose_step='subject')
            window=MagicMock()
            with patch.object(companion,'email_draft',draft),patch.object(companion,'APP_WINDOW',window),patch.object(companion,'speak'):
                companion.handle('go to two','email_draft')
                self.assertEqual(draft.compose_step,'recipient')
                self.assertEqual(draft.subject,'Hello')
                window.focus_email_field.assert_called_with('recipient','wrong@example.com')
                companion.handle('right@example.com','email_draft',typed=True)
                self.assertEqual(draft.recipient,'right@example.com')

    def test_radio_prompt_search_followup_and_podcast_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub=MediaHub(tmp,fetch_json=lambda url: [{'name':'WGN Radio','url':'https://radio.example/wgn',
                                                     'country':'US'}])
            self.assertIn('What station',hub.command('search radio'))
            self.assertIn('WGN Radio',hub.command('WGN'))
            hub.shows=[{'name':'The History Show','feed':'https://pod.example/feed'}]
            hub._save('podcast-subscriptions.json', hub.shows)
            self.assertIn('The History Show',hub.command('list podcasts'))
            import companion
            with patch.object(companion,'MEDIA_HUB',hub),patch.object(companion,'speak') as speech:
                self.assertEqual(companion.handle('radio','awake'),'media')
                self.assertNotIn('subscriptions',speech.call_args.args[0].lower())
                companion.handle('search radio','media')
                self.assertIn('What station',speech.call_args.args[0])
                companion.handle('WGN','media')
                self.assertIn('WGN Radio',speech.call_args.args[0])

    def test_email_field_navigation_and_manual_address_from_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft=VoiceEmail(Path(tmp)/'Email Drafts',compose_step='recipient')
            self.assertIn('subject',draft.process('person@example.com'))
            draft.process('A subject')
            draft.process('A short body.')
            self.assertIn('To field',draft.process('go to to'))
            self.assertEqual(draft.compose_step,'recipient')
            self.assertIn('Recipient recorded',draft.process('typed@example.com'))
            self.assertEqual(draft.recipient,'typed@example.com')
            self.assertIn('Subject field',draft.process('go to subject'))
            draft.process('Corrected subject')
            self.assertEqual(draft.subject,'Corrected subject')
            self.assertIn('CC field',draft.process('go to cc'))
            draft.process('copy@example.com')
            self.assertEqual(draft.cc,['copy@example.com'])
            self.assertIn('BCC field',draft.process('go to bcc'))
            draft.process('hidden@example.com')
            self.assertEqual(draft.bcc,['hidden@example.com'])
            self.assertIn('Message body',draft.process('go to body'))
            self.assertNotIn('go to',draft.paragraphs[0].text)

    def test_email_navigation_is_a_command_in_dictation_only_mode(self):
        import companion
        with tempfile.TemporaryDirectory() as tmp:
            draft=VoiceEmail(Path(tmp)/'Email Drafts',recipient='old@example.com',subject='Old',compose_step='body')
            with patch.object(companion,'email_draft',draft),patch.object(companion,'INPUT_MODE','dictation'), \
                 patch.object(companion,'speak') as speech:
                self.assertEqual(companion.handle('go to to','email_draft'),'email_draft')
                self.assertIn('To field',speech.call_args.args[0])
                companion.handle('typed@example.com','email_draft')
                self.assertEqual(draft.recipient,'typed@example.com')
                companion.handle('go to subject','email_draft')
                companion.handle('New subject','email_draft')
                self.assertEqual(draft.subject,'New subject')

    def test_email_field_commands_focus_the_typing_box_automatically(self):
        import companion
        with tempfile.TemporaryDirectory() as tmp:
            draft=VoiceEmail(Path(tmp)/'Email Drafts',compose_step='body')
            window=MagicMock()
            with patch.object(companion,'email_draft',draft),patch.object(companion,'APP_WINDOW',window), \
                 patch.object(companion,'INPUT_MODE','mixed'),patch.object(companion,'speak'):
                for command,field in (('go to to','recipient'),('go to cc','cc'),
                                      ('go to bcc','bcc'),('go to subject','subject')):
                    companion.handle(command,'email_draft')
                    window.focus_email_field.assert_called_with(field, draft.recipient if field == "recipient" else draft.subject if field == "subject" else "")
                companion.handle('typed@example.com','email_draft',typed=True)
                self.assertEqual(draft.compose_step,'body')

    def test_recipient_contact_subject_body_cc_bcc_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            AddressBook(root).save('Bob', 'bob@example.com')
            draft = VoiceEmail(root/'Email Drafts', provider='gmail', compose_step='recipient')
            self.assertIn('What is the subject?', draft.process('Bob'))
            self.assertEqual(draft.recipient, 'bob@example.com')
            self.assertIn('What would you like', draft.process('Meeting tomorrow'))
            self.assertEqual(draft.subject, 'Meeting tomorrow')
            self.assertIn('Who should receive', draft.process('cc'))
            self.assertIn('CC recorded', draft.process('helper@example.com'))
            self.assertIn('Who should receive', draft.process('bcc'))
            self.assertIn('BCC recorded', draft.process('private@example.com'))
            draft.process('See you soon.')
            reopened = VoiceEmail.open_existing(root/'Email Drafts', draft.title)
            self.assertEqual(reopened.bcc, ['private@example.com'])
            self.assertEqual(reopened.cc, ['helper@example.com'])
            self.assertEqual(reopened.paragraphs[0].text, 'See you soon.')
            mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(json.loads(gmail_request(reopened,'token').data)['raw']))
            self.assertEqual(mime['Bcc'], 'private@example.com')
            graph = json.loads(microsoft_request(reopened,'token').data)['message']
            self.assertEqual(graph['bccRecipients'][0]['emailAddress']['address'], 'private@example.com')

    def test_google_sync_imports_and_uploads_without_another_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = AddressBook(tmp, 'me@example.com')
            book.save('Local friend', 'local@example.com')
            remote = {'connections':[{'names':[{'displayName':'Google friend'}],
                                      'emailAddresses':[{'value':'google@example.com'}]}]}
            class Selected:
                def __init__(self, _): pass
                def selected(self): return ('gmail','me@example.com')
            calls = []
            def api(url, token, data=None):
                if data is not None: calls.append(data); return {}
                return remote
            with patch('google_contacts._token', return_value='token'), patch('google_contacts.ProtectedStore', Selected), patch('google_contacts._api', side_effect=api):
                self.assertIn('1 contacts added here; 1 local contacts added to Google', sync(tmp))
            self.assertEqual(book.get('Google friend')['address'], 'google@example.com')
            self.assertEqual(calls[0]['emailAddresses'][0]['value'], 'local@example.com')

    def test_link_picker_next_previous_confirm_and_spoken_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeBackend()
            web = WebSession(tmp, backend)
            web.open('example.com')
            first=web.command('list links')
            self.assertIn('Log in (L), 1 of 2', first)
            self.assertNotIn('Transfer between accounts', first)
            self.assertIn('Transfer between accounts', web.command('next link'))
            self.assertIn('Log in', web.command('previous link'))
            self.assertIn('Page:', web.command('confirm that'))
            self.assertEqual(backend.activated, ['0'])
            self.assertIn('Transfer between accounts', web.command('open link number two'))
            self.assertIn('Page:', web.command('okay'))
            self.assertEqual(backend.activated, ['0','1'])


if __name__ == '__main__': unittest.main()


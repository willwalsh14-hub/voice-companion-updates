import base64
import json
import unittest

from mailbox_access import MailboxClient, MailboxError, gmail_body, message_date
from mail_voice import MailSession
from yahoo_mailbox import YahooMailbox, encode_folder_name, decode_folder_name
from unittest.mock import patch


class Reply:
    def __init__(self, value):
        self.body = json.dumps(value).encode()
        self.offset = 0
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, length):
        chunk = self.body[self.offset:self.offset + length]
        self.offset += len(chunk)
        return chunk


class MailboxTests(unittest.TestCase):
    def test_short_mark_and_select_and_automatic_refresh_after_changes(self):
        import tempfile
        class FakeClient:
            rows = ['a', 'b', 'c']
            fetches = 0
            def __init__(self, *args): pass
            def folders(self): return [('Inbox', 'INBOX'), ('Saved', 'saved')]
            def list_messages(self, folder, cursor=None, limit=10):
                self.__class__.fetches += 1
                return ([{'id': ident, 'from': ident + '@example.com', 'subject': ident}
                         for ident in self.rows], None)
            def trash(self, ids):
                self.__class__.rows = [ident for ident in self.rows if ident not in ids]
            def move(self, ids, source, destination): self.trash(ids)
        with tempfile.TemporaryDirectory() as folder, patch('mail_voice.account_token', return_value=('me@example.com', 'token')):
            session = MailSession('gmail', folder, FakeClient)
            session.process('list messages')
            self.assertIn('Marked message 1', session.process('mark'))
            session.process('next message')
            self.assertIn('Marked message 2', session.process('select'))
            before = FakeClient.fetches
            session.process('delete')
            response = session.process('yes')
            self.assertIn('2 messages moved', response)
            self.assertIn('Sender name unavailable', response)
            self.assertNotIn('c@example.com',response)
            self.assertGreater(FakeClient.fetches, before)
            self.assertEqual([row['id'] for row in session.rows], ['c'])
            self.assertFalse(session.marked_ids)
            session.process('move to Saved')
            self.assertIn('No messages on this page', session.process('yes'))
            self.assertEqual(session.rows, [])

    def test_email_list_size_defaults_to_ten_and_persists_options(self):
        import tempfile
        class FakeClient:
            limits = []
            def __init__(self, *args): pass
            def list_messages(self, folder, cursor=None, limit=10):
                self.limits.append(limit)
                return ([{'id':'a','from':'a@example.com','subject':'Mail'}], None)
        with tempfile.TemporaryDirectory() as folder, patch('mail_voice.account_token', return_value=('a@example.com','token')):
            session = MailSession('gmail', folder, FakeClient)
            session.process('open inbox')
            self.assertEqual(FakeClient.limits[-1], 10)
            for size in (20,30,40,50,100):
                self.assertIn('Showing ' + str(size), session.process('set email list size ' + str(size)))
                self.assertEqual(FakeClient.limits[-1], size)
            self.assertIn('Choose 10', session.process('set email list size 25'))
            self.assertEqual(FakeClient.limits[-1], 100)
            self.assertIn('All messages are available', session.process('set email list size all'))
            self.assertEqual(FakeClient.limits[-1], 10)
            self.assertTrue(MailSession('gmail', folder, FakeClient).all_messages)

    def test_natural_list_size_phrases_are_saved(self):
        import tempfile
        class FakeClient:
            def __init__(self, *args): pass
            def list_messages(self, folder, cursor=None, limit=10):
                return ([{'id':'one','from':'friend@example.com','subject':'Hello'}], None)
        with tempfile.TemporaryDirectory() as folder, patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('gmail', folder, FakeClient)
            for phrase, count in [('list 20',20), ('list 30 messages',30),
                                  ('40 number of messages',40), ('set list to 50',50)]:
                self.assertIn('Showing ' + str(count), session.process(phrase), phrase)
                self.assertEqual(MailSession('gmail', folder, FakeClient).list_size, count)

    def test_current_message_actions_ignore_marked_messages_and_folder_prompts(self):
        import tempfile
        class FakeClient:
            actions = []
            def __init__(self, *args): pass
            def folders(self): return [('Inbox','INBOX'), ('Saved','saved')]
            def list_messages(self, folder, cursor=None, limit=10):
                return ([{'id':'a','from':'a@example.com','subject':'One'},
                         {'id':'b','from':'b@example.com','subject':'Two'}], None)
            def trash(self, ids): self.actions.append(('trash', ids))
            def move(self, ids, source, destination): self.actions.append(('move', ids, source, destination))
        FakeClient.actions = []
        with tempfile.TemporaryDirectory() as folder, patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('gmail', folder, FakeClient)
            session.process('list messages')
            session.process('mark message')
            session.process('next message')
            self.assertIn('Marked message 2', session.process('select message'))
            session.process('unmark message')
            self.assertEqual(session.selected, {1})
            self.assertIn('this message to Trash', session.process('delete message'))
            session.process('no')
            self.assertIn('the marked message to Trash', session.process('delete'))
            session.process('no')
            self.assertIn('this message to Saved', session.process('move message to Saved'))
            session.process('no')
            self.assertIn('the marked message to Saved', session.process('move to Saved'))
            self.assertEqual(session.pending[1], ['a'])
            session.process('no')
            self.assertIn('this message to Trash', session.process('delete this message'))
            session.process('no')
            session.process('mark message')
            self.assertIn('2 marked messages to Trash', session.process('delete'))
            session.process('no')
            self.assertIn('move the messages there', session.process('move'))
            session.process('cancel')
            session.process('unmark message')
            session.process('clear selection')
            self.assertIn('this message', session.process('delete'))
            self.assertIn('1 message moved', session.process('yes'))
            self.assertEqual(FakeClient.actions[-1], ('trash',['b']))
            session.process('list messages')
            self.assertIn('move this message there', session.process('move message'))
            self.assertIn('Saved', session.process('next folder'))
            self.assertIn('this message to Saved', session.process('that one'))
            session.process('yes')
            self.assertEqual(FakeClient.actions[-1], ('move',['a'],'INBOX','saved'))
            session.process('list messages')
            self.assertIn('delete it', session.process('delete folder'))
            self.assertIn('delete it', session.process('next folder'))

    def test_mailbox_commands_reach_session_from_companion(self):
        import companion
        class Session:
            folder_picker = folder_choice = None
            view = 'folder'
            def process(self, text): return 'Mail handled ' + text
        with patch.object(companion, 'mail_session', Session()), patch.object(companion, 'speak') as speech:
            for phrase in ('delete', 'delete message', 'mark message', 'select message',
                           'move message', 'move message to Saved', 'list 20 messages',
                           '20 number of messages'):
                self.assertEqual(companion.handle(phrase, 'mailbox'), 'mailbox')
                self.assertEqual(speech.call_args.args[0], 'Mail handled ' + phrase)

    def test_marks_survive_next_message_across_page_boundary(self):
        import tempfile
        class FakeClient:
            actions = []
            def __init__(self, *args): pass
            def list_messages(self, folder, cursor=None, limit=10):
                ident = 'b' if cursor else 'a'
                return ([{'id':ident,'from':ident+'@example.com','subject':'Mail'}],
                        None if cursor else 'next-page')
            def trash(self, ids): self.actions.append(ids)
        FakeClient.actions = []
        with tempfile.TemporaryDirectory() as folder, patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('gmail', folder, FakeClient)
            session.process('open inbox')
            session.process('mark message')
            session.process('next message')
            session.process('mark message')
            self.assertIn('Marked 2 messages', session.process('what is selected'))
            self.assertIn('2 marked messages to Trash', session.process('delete'))
            session.process('yes')
            self.assertEqual(FakeClient.actions[-1], ['a','b'])

    def test_plain_move_uses_folder_picker_for_marked_messages(self):
        import tempfile
        class FakeClient:
            actions = []
            def __init__(self, *args): pass
            def list_messages(self, folder, cursor=None, limit=10):
                return ([{'id':'a','from':'a@example.com','subject':'One'},
                         {'id':'b','from':'b@example.com','subject':'Two'}], None)
            def folders(self): return [('Inbox','INBOX'), ('Saved','saved')]
            def move(self, ids, source, destination): self.actions.append((ids, source, destination))
        FakeClient.actions = []
        with tempfile.TemporaryDirectory() as folder, patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('gmail', folder, FakeClient)
            session.process('list messages')
            session.process('mark message')
            session.process('next message')
            session.process('mark message')
            self.assertIn('that one to move the messages there', session.process('move'))
            self.assertIn('Saved', session.process('next'))
            self.assertIn('2 marked messages to Saved', session.process('that one'))
            self.assertEqual(FakeClient.actions, [])
            session.process('yes')
            self.assertEqual(FakeClient.actions, [(['a','b'],'INBOX','saved')])

    def test_thousand_message_choice_reports_progress_in_bounded_batches(self):
        import tempfile
        class FakeClient:
            limits = []
            def __init__(self, *args): pass
            def list_messages(self, folder, cursor=None, limit=10):
                self.limits.append(limit)
                page = int(cursor or 0)
                return ([{'id':str(page * limit + n),'from':'a@example.com','subject':'Mail'}
                         for n in range(limit)], str(page + 1) if page < 9 else None)
        feedback = []
        with tempfile.TemporaryDirectory() as folder, patch('mail_voice.account_token', return_value=('a@example.com','token')):
            session = MailSession('gmail', folder, FakeClient, progress=feedback.append)
            self.assertIn('Showing 1000', session.process('set list to 1000'))
            self.assertEqual((len(session.rows), FakeClient.limits), (1000, [100] * 10))
            self.assertIn('Getting messages', feedback[0])
            self.assertTrue(any('Retrieved 200' in message for message in feedback))
            self.assertIn('1000 messages', session.process('email list size'))
            self.assertIn('Showing 20', session.process('set to 20'))
            self.assertEqual(FakeClient.limits[-1], 20)

    def test_email_navigation_preempts_audio_seek_and_opens_sent_items(self):
        import companion
        class Session:
            folder_picker = folder_choice = None
            def process(self, phrase): return 'Mail handled ' + phrase
        with patch.object(companion, 'mail_session', Session()), patch.object(companion, 'MEDIA_HUB') as media, \
             patch.object(companion, 'speak') as speech:
            for phrase in ('back to inbox', 'go to sent', 'go to inbox'):
                self.assertEqual(companion.handle(phrase, 'mailbox'), 'mailbox')
                self.assertEqual(speech.call_args.args[0], 'Mail handled ' + phrase)
            media.command.assert_not_called()

    def test_sent_alias_and_page_boundary_navigation(self):
        class FakeClient:
            def __init__(self, *args): pass
            def folders(self): return [('Inbox', 'inbox'), ('Sent Items', 'sentitems')]
            def list_messages(self, ident, cursor=None, limit=10):
                return ([{'id': str(cursor or 'first'), 'from': 'person@example.com',
                          'subject': 'Subject', 'date': 'Today'}], 'next' if cursor is None else None)
        with patch('mail_voice.account_token', return_value=('me@example.com', 'token')):
            session = MailSession('outlook', '/unused', FakeClient)
            self.assertIn('Sent Items.', session.process('go to sent'))
            self.assertEqual(session.folder_id, 'sentitems')
            session.process('next message')
            self.assertEqual(session.rows[0]['id'], 'next')
            session.process('previous message')
            self.assertEqual(session.rows[0]['id'], 'first')

    def test_mailbox_short_open_phrases_reach_session_before_voice_picker(self):
        import companion
        class Session:
            folder_picker=None
            folder_choice=None
            view='folder'
            def process(self, command): return 'Opened '+command
        with patch.object(companion,'mail_session',Session()), \
             patch.object(companion,'VOICE_PICK_INDEX',None), \
             patch.object(companion,'INPUT_MODE','dictation'), \
             patch.object(companion,'speak') as speech:
            for phrase in ('that one','okay','confirm','confirm that'):
                self.assertEqual(companion.handle(phrase,'mailbox'),'mailbox')
                self.assertEqual(speech.call_args.args[0],'Opened '+phrase)

    def test_gmail_listing_read_and_trash(self):
        calls = []
        raw = base64.urlsafe_b64encode(b'From: friend@example.com\nSubject: Hello\nContent-Type: text/plain; charset=utf-8\n\nA readable message.').decode()
        def open_request(req, timeout):
            calls.append((req.full_url, req.get_method(), req.data))
            if 'format=metadata' in req.full_url:
                return Reply({'payload': {'headers': [{'name':'From','value':'friend@example.com'},
                                                      {'name':'Subject','value':'Hello'}]}, 'labelIds':['UNREAD','INBOX'],
                              'internalDate':'1783036800000'})
            if 'format=minimal' in req.full_url: return Reply({'labelIds':['INBOX']})
            if 'format=raw' in req.full_url: return Reply({'raw':raw})
            if '?labelIds=' in req.full_url: return Reply({'messages':[{'id':'abc'}], 'nextPageToken':'next'})
            return Reply({})
        client = MailboxClient('gmail', 'token', open_request)
        rows, cursor = client.list_messages('INBOX')
        self.assertEqual((rows[0]['subject'], rows[0]['unread'], cursor), ('Hello', True, 'next'))
        self.assertIn('2026', rows[0]['date'])
        self.assertEqual(client.read_message('abc').strip(), 'A readable message.')
        client.move(['abc'], 'INBOX', 'Label_123')
        self.assertEqual(json.loads(calls[-1][2]), {'addLabelIds':['Label_123'], 'removeLabelIds':['INBOX']})
        client.trash(['abc'])
        self.assertTrue(calls[-1][0].endswith('/messages/abc/trash'))

    def test_graph_page_host_restricted_and_move(self):
        calls = []
        def open_request(req, timeout):
            calls.append((req.full_url, req.get_method(), req.data))
            return Reply({'value':[{'id':'graph-id','subject':'News','from':{'emailAddress':{'address':'a@example.com'}},'isRead':False,
                                    'receivedDateTime':'2026-07-03T12:00:00Z'}],
                          '@odata.nextLink':'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=10'})
        client = MailboxClient('outlook', 'token', open_request)
        rows, next_link = client.list_messages('inbox')
        self.assertEqual(rows[0]['from'], 'a@example.com')
        self.assertTrue(rows[0]['unread'])
        self.assertIn('2026', rows[0]['date'])
        client.list_messages('inbox', next_link)
        with self.assertRaises(MailboxError):
            client.list_messages('inbox', 'https://graph.microsoft.com.evil.example/steal')
        client.move(['graph-id'], 'inbox', 'archive-id')
        self.assertEqual(json.loads(calls[-1][2]), {'destinationId':'archive-id'})
        client.trash(['graph-id'])
        self.assertEqual(json.loads(calls[-1][2]), {'destinationId':'deleteditems'})

    def test_graph_nested_folder_requests(self):
        calls = []
        def open_request(req, timeout):
            calls.append((req.full_url, req.get_method(), req.data))
            if '/childFolders?' in req.full_url:
                return Reply({'value':[{'id':'child','displayName':'2026','childFolderCount':0}]})
            if 'mailFolders?' in req.full_url:
                return Reply({'value':[{'id':'parent','displayName':'Projects','childFolderCount':1}]})
            return Reply({'id':'new-folder'})
        client = MailboxClient('outlook','token',open_request)
        self.assertIn(('Projects/2026','child'), client.folders())
        client.create_folder('Receipts','parent','Projects/')
        self.assertTrue(calls[-1][0].endswith('/mailFolders/parent/childFolders'))
        client.rename_folder('child','2027','Projects/')
        self.assertEqual(json.loads(calls[-1][2]), {'displayName':'2027'})
        client.move_folder('child','Projects/2026','other','Archive/')
        self.assertEqual(json.loads(calls[-1][2]), {'destinationId':'other'})
        client.delete_folder('child')
        self.assertEqual(calls[-1][1], 'DELETE')

    def test_html_body_omits_script(self):
        raw = base64.urlsafe_b64encode(b'Content-Type: text/html; charset=utf-8\n\n<p>Hello</p><script>secret()</script><p>World</p>').decode()
        self.assertEqual(gmail_body(raw), 'Hello\nWorld')

    def test_mail_dates_and_stale_message(self):
        self.assertEqual(message_date('03-Jul-2026 12:00:00 +0000'), 'July 3, 2026')
        self.assertEqual(message_date('not a date'), 'unavailable')
        client = MailboxClient('gmail', 'token', lambda req, timeout: Reply({'id':'other','raw':'aGVsbG8='}))
        with self.assertRaises(MailboxError): client.read_message('original')

    def test_reply_all_excludes_own_account(self):
        raw = base64.urlsafe_b64encode(
            b'From: Friend <friend@example.com>\nTo: me@example.com, Other <other@example.com>\n'
            b'Subject: Hello\nMessage-ID: <original@example.com>\n\nBody').decode()
        client = MailboxClient('gmail', 'token', lambda req, timeout: Reply({'raw':raw,'threadId':'thread'}))
        context = client.response_context('source', 'reply_all', 'me@example.com')
        self.assertEqual(context['recipient'], 'friend@example.com')
        self.assertEqual(context['cc'], ['other@example.com'])
        self.assertEqual(context['subject'], 'Re: Hello')

    def test_voice_selection_requires_confirmation(self):
        class FakeClient:
            actions = []
            def __init__(self, provider, token): pass
            def folders(self): return [('Inbox','INBOX'), ('Saved','Label_1')]
            def list_messages(self, folder, cursor=None, limit=10):
                return ([{'id':'a','from':'a@example.com','subject':'First','unread':True},
                         {'id':'b','from':'b@example.com','subject':'Second','unread':False}], None)
            def read_message(self, ident, folder=None): return 'Body ' + ident
            def move(self, ids, source, dest): self.actions.append(('move', ids, source, dest))
            def trash(self, ids): self.actions.append(('trash', ids))
        with patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('gmail', '/unused', FakeClient)
            self.assertIn('Sender name unavailable. First. unavailable. 1 of 2.', session.process('open inbox'))
            self.assertIn('Body a', session.process('read message 1'))
            self.assertNotIn('First. unavailable', session.process('open message'))
            self.assertIn('First. unavailable', session.process('message details'))
            self.assertIn('Body a', session.process('start reading'))
            self.assertEqual(session.process('next character'),'o')
            self.assertEqual(session.process('next word'),'a')
            self.assertIn('Body a', session.process('open message one'))
            self.assertIn('Second. unavailable', session.process('next message'))
            self.assertIn('First. unavailable', session.process('previous'))
            self.assertIn('Second. unavailable', session.process('next'))
            self.assertIn('Body b', session.process('open current message'))
            self.assertIn('First. unavailable', session.process('previous message'))
            self.assertIn('Body a', session.process('read current message'))
            self.assertIn('Body a', session.process('open message'))
            self.assertIn('Body a', session.process('open that'))
            for phrase in ('that one', 'okay', 'confirm', 'confirm that'):
                session.process('back to folder')
                session.process('next message')
                self.assertIn('Body b', session.process(phrase), phrase)
                session.process('previous message')
            self.assertIn('Back to Inbox', session.process('back to folder'))
            self.assertIn('message list', session.process('which folder'))
            self.assertIn('Sender name unavailable', session.process('back to inbox'))
            session.process('select messages one through two')
            self.assertIn('Is that right? Say yes or no.', session.process('move selected to Saved'))
            session.process('cancel')
            self.assertEqual(FakeClient.actions, [])
            session.process('move selected to Saved')
            self.assertIn('Say yes or no', session.process('confirm that'))
            self.assertEqual(FakeClient.actions, [])
            self.assertIn('2 messages moved', session.process('confirm move'))
            self.assertEqual(FakeClient.actions, [('move',['a','b'],'INBOX','Label_1')])
            session.process('list messages')
            session.process('select message 1')
            self.assertIn('Is that right? Say yes or no.', session.process('delete selected'))
            session.process('confirm delete')
            self.assertEqual(FakeClient.actions[-1], ('trash',['a']))

    def test_folder_changes_confirm_exact_paths_and_refuse_nonempty(self):
        class FakeClient:
            actions = []
            def __init__(self, provider, token): pass
            def folders(self): return [('Inbox','inbox'), ('Projects','p'), ('Projects/2026','c'), ('Archive','a'), ('Letters','l')]
            def folder_info(self, ident):
                return {'totalItemCount': 1 if ident == 'c' else 0, 'childFolderCount': 0}
            def create_folder(self, *args): self.actions.append(('create', args))
            def rename_folder(self, *args): self.actions.append(('rename', args))
            def move_folder(self, *args): self.actions.append(('move', args))
            def delete_folder(self, *args): self.actions.append(('delete', args))
        with patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('outlook', '/unused', FakeClient)
            session.process('list folders')
            self.assertIn('Projects/Receipts', session.process('create folder Receipts inside Projects'))
            session.process('cancel')
            self.assertIn('Nothing', session.process('confirm create folder'))
            session.process('create folder Receipts inside Projects')
            session.process('confirm create folder')
            self.assertIn(('create', ('Receipts', 'p', 'Projects/')), FakeClient.actions)
            session.process('list folders')
            self.assertIn('not empty', session.process('delete folder Projects/2026'))
            self.assertIn('cannot be moved', session.process('move folder Projects into Projects/2026'))
            self.assertIn('Is that right? Say yes or no.', session.process('rename folder Letters to Saved'))
            session.process('confirm rename folder')
            self.assertIn(('rename', ('l','Saved','')), FakeClient.actions)
            session.process('list folders')
            self.assertIn('Is that right? Say yes or no.', session.process('delete folder Letters'))
            session.process('confirm delete folder')
            self.assertIn(('delete', ('l',)), FakeClient.actions)

    def test_go_to_folder_directly_and_by_full_path(self):
        class FakeClient:
            def __init__(self, *args): pass
            def folders(self): return [('Inbox', 'inbox'), ('Important', 'important'), ('Receipts', 'r'),
                                       ('Projects/2026', 'p26'), ('Archive/2026', 'a26')]
            def list_messages(self, ident, cursor=None, limit=10):
                return ([{'id': ident, 'from': 'friend@example.com',
                          'subject': 'Hello', 'date': 'September 26, 2026'}], None)
        with patch('mail_voice.account_token', return_value=('me@example.com', 'token')):
            session = MailSession('outlook', '/unused', FakeClient)
            self.assertIn('Receipts.', session.process('go to folder Receipts'))
            self.assertEqual(session.folder_id, 'r')
            self.assertIn('Important.', session.process('go to important'))
            self.assertEqual(session.folder_id, 'important')
            self.assertIn('Inbox.', session.process('go to inbox'))
            self.assertIn('I see 2 folders named 2026', session.process('go to 2026'))
            self.assertIn('2026 inside of Projects', session.process('current folder'))
            self.assertIn('Projects/2026', session.process('okay'))
            self.assertEqual(session.folder_id, 'p26')
            self.assertIn('I see 2 folders named 2026', session.process('go to folder 2026'))
            self.assertIn('2026 inside of Archive', session.process('next folder'))
            self.assertIn('Archive/2026.', session.process('inside of Archive'))
            self.assertIn('Projects/2026.', session.process('go to folder Projects slash 2026'))
            self.assertEqual(session.folder_id, 'p26')
            self.assertIn('Projects/2026.', session.process('go to Projects slash 2026'))
            self.assertIn('Archive/2026.', session.process('open folder Archive slash 2026'))

    def test_duplicate_root_and_nested_folder_spoken_choice(self):
        class FakeClient:
            def __init__(self, *args): pass
            def folders(self): return [('Inbox', 'inbox'), ('2026', 'root'), ('Projects/2026', 'nested')]
            def list_messages(self, ident, cursor=None, limit=10): return ([], None)
        with patch('mail_voice.account_token', return_value=('me@example.com', 'token')):
            session = MailSession('outlook', '/unused', FakeClient)
            prompt = session.process('go to 2026')
            self.assertIn('I see 2 folders named 2026. First choice: 2026.', prompt)
            self.assertNotIn('inside of Projects', prompt)
            self.assertIn('inside of Projects', session.process('next'))
            self.assertIn('Projects/2026', session.process('2026 inside of Projects'))
            self.assertEqual(session.folder_id, 'nested')
            session.process('go to 2026')
            self.assertIn('No messages on this page of 2026', session.process('2026'))
            self.assertEqual(session.folder_id, 'root')

    def test_go_to_unique_nested_folder_by_short_name(self):
        class FakeClient:
            def __init__(self, *args): pass
            def folders(self): return [('Inbox', 'inbox'), ('Projects/2026', 'p26')]
            def list_messages(self, ident, cursor=None, limit=10): return ([], None)
        with patch('mail_voice.account_token', return_value=('me@example.com', 'token')):
            session = MailSession('outlook', '/unused', FakeClient)
            self.assertIn('Projects/2026', session.process('go to 2026'))
            self.assertEqual(session.folder_id, 'p26')

    def test_spoken_folder_chooser_for_navigation_and_changes(self):
        class FakeClient:
            actions = []
            names = [('Inbox', 'inbox'), ('Receipts', 'r'), ('Archive', 'a')]
            def __init__(self, *args): pass
            def folders(self): return self.names
            def list_messages(self, ident, cursor=None, limit=10):
                return ([{'id': 'msg', 'from': 'a@example.com', 'subject': 'Hi', 'date': 'September 26, 2026'}], None)
            def folder_info(self, ident): return {'totalItemCount': 0, 'childFolderCount': 0}
            def move(self, *args): self.actions.append(('message_move', args))
            def create_folder(self, *args): self.actions.append(('create', args))
            def rename_folder(self, *args): self.actions.append(('rename', args))
            def move_folder(self, *args): self.actions.append(('folder_move', args))
            def delete_folder(self, *args): self.actions.append(('delete', args))
        with patch('mail_voice.account_token', return_value=('me@example.com', 'token')):
            session = MailSession('outlook', '/unused', FakeClient)
            self.assertIn('Folder 1: Inbox', session.process('go to folder'))
            self.assertIn('Receipts', session.process('move down'))
            self.assertIn('Receipts.', session.process('okay'))
            self.assertEqual(session.folder_id, 'r')
            session.process('select message 1')
            session.process('move selected to folder')
            session.process('next folder')
            session.process('next folder')
            self.assertIn('Archive', session.process('okay'))
            self.assertEqual(FakeClient.actions, [])
            self.assertIn('1 message moved', session.process('confirm move'))
            self.assertEqual(FakeClient.actions[-1], ('message_move', (['msg'], 'r', 'a')))
            session.process('rename folder')
            session.process('next folder')
            self.assertIn('rename to', session.process('okay'))
            self.assertIn('Is that right? Say yes or no.', session.process('rename to Bills'))
            session.process('cancel')
            session.process('move folder')
            session.process('next folder')
            self.assertIn('Folder 1:', session.process('okay'))
            session.process('next folder')
            session.process('next folder')
            self.assertIn('Is that right? Say yes or no.', session.process('okay'))
            session.process('cancel')
            session.process('create folder 2026 inside')
            session.process('next folder')
            self.assertIn('Receipts/2026', session.process('okay'))
            session.process('confirm create folder')
            self.assertEqual(FakeClient.actions[-1], ('create', ('2026', 'r', 'Receipts/')))
            session.process('delete folder')
            session.process('next folder')
            self.assertIn('Is that right? Say yes or no.', session.process('okay'))
            session.process('cancel')
            session.process('go to folder')
            self.assertIn('canceled', session.process('go back').lower())

    def test_folder_confirmation_cancels_if_destination_changes(self):
        class FakeClient:
            folders_now = [('Inbox','inbox'),('Letters','l'),('Projects','p')]
            actions = []
            def __init__(self, *args): pass
            def folders(self): return self.folders_now
            def folder_info(self, ident): return {'totalItemCount':0,'childFolderCount':0}
            def move_folder(self, *args): self.actions.append(args)
        with patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('outlook','/unused',FakeClient)
            session.process('list folders')
            self.assertIn('Is that right? Say yes or no.', session.process('move folder Letters into Projects'))
            FakeClient.folders_now = [('Inbox','inbox'),('Letters','l'),('Projects New','p')]
            self.assertIn('canceled', session.process('confirm move folder'))
            self.assertEqual(FakeClient.actions, [])

    def test_yahoo_uses_current_folder_and_safe_uid_move(self):
        self.assertEqual(decode_folder_name(encode_folder_name('Café & notes')), 'Café & notes')
        class FakeIMAP:
            capabilities = (b'IMAP4REV1', b'MOVE')
            calls = []
            def __init__(self, *args, **kwargs): pass
            def login(self, *args): pass
            def logout(self): pass
            def select(self, folder, readonly=False):
                self.calls.append(('select', folder, readonly)); return 'OK', [b'1']
            def uid(self, command, uid, *args):
                self.calls.append((command, uid, args))
                if command == 'FETCH':
                    return 'OK', [(b'1 (BODY[] {25}', b'From: a@example.com\n\nHello')]
                return 'OK', [b'']
        client = YahooMailbox('me@yahoo.com', 'app-password', FakeIMAP)
        self.assertEqual(client.read_message('123', 'Saved'), 'Hello')
        self.assertIn(('select', '"Saved"', True), FakeIMAP.calls)
        client.move(['123'], 'Saved', 'Archive')
        self.assertIn(('MOVE', '123', ('"Archive"',)), FakeIMAP.calls)
        with self.assertRaises(MailboxError): client.move(['1:100'], 'Saved', 'Archive')

    def test_companion_reply_returns_to_same_folder(self):
        import tempfile
        from pathlib import Path
        import companion
        from unittest.mock import patch
        class Session:
            provider = 'gmail'
            folder_name = 'Letters'
            view = 'message'
            def response_context(self, action):
                return {'action':action, 'source_id':'id', 'thread_id':'thread',
                        'recipient':'friend@example.com', 'cc':[], 'subject':'Re: Hi'}
            def process(self, text):
                self.view = 'folder'
                return 'Back to Letters.'
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(companion, 'APP', Path(folder)), \
             patch.object(companion, 'mail_session', Session()), \
             patch.object(companion, 'speak') as speech:
            self.assertEqual(companion.handle('Reply', 'mailbox'), 'email_draft')
            self.assertEqual(companion.email_draft.recipient, 'friend@example.com')
            self.assertEqual(companion.handle('Go back', 'email_draft'), 'mailbox')
            self.assertIn('Letters', speech.call_args.args[0])
            self.assertEqual(companion.handle('Go back', 'mailbox'), 'awake')

    def test_voice_can_return_to_previous_message_page(self):
        class FakeClient:
            cursors = []
            def __init__(self, *args): pass
            def list_messages(self, folder, cursor=None, limit=10):
                self.cursors.append(cursor)
                return ([{'id':'a','from':'a@example.com','subject':'Mail','unread':False}],
                        'second' if cursor is None else None)
        with patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('gmail','/unused',FakeClient)
            session.process('open inbox')
            session.process('get more emails')
            session.process('go back a list')
            self.assertEqual(FakeClient.cursors, [None, 'second', None])

    def test_long_message_can_be_read_to_end(self):
        class FakeClient:
            def __init__(self, *args): pass
            def list_messages(self, folder, cursor=None, limit=10):
                return ([{'id':'a','from':'a@example.com','subject':'Long','unread':False}], None)
            def read_message(self, ident, folder): return 'word ' * 2500
        with patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('gmail','/unused',FakeClient)
            session.process('open inbox')
            first = session.process('read message 1')
            self.assertIn('continue reading', first)
            while session.body_offset < len(session.body_text):
                last = session.process('continue reading')
            self.assertIn('End of message', last)
            self.assertIn('continue reading', session.process('read from beginning'))

    def test_next_previous_message_cross_page_boundary(self):
        class FakeClient:
            def __init__(self, *args): pass
            def list_messages(self, folder, cursor=None, limit=10):
                ident = 'first' if cursor is None else 'second'
                return ([{'id':ident,'from':'a@example.com','subject':ident,'unread':False}],
                        'page-two' if cursor is None else None)
            def read_message(self, ident, folder): return 'Body ' + ident
        with patch('mail_voice.account_token', return_value=('me@example.com','token')):
            session = MailSession('gmail','/unused',FakeClient)
            session.process('open inbox')
            session.process('read message one')
            self.assertIn('second. unavailable', session.process('next message'))
            self.assertIn('Body second', session.process('open current message'))
            self.assertIn('first. unavailable', session.process('previous message'))


if __name__ == '__main__': unittest.main()

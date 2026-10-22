"""Release checks for the October 2026 launch review ("Should do before launch")."""
import base64
import email.message
import json
import queue
import re
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import companion


class ManualUpdateCheckTests(unittest.TestCase):
    """A manual check may interrupt the current screen once; later automatic offers wait."""

    def release(self):
        return {'schema': 1, 'version': '9.9.9-test', 'installer_url': 'https://example.org/Setup.exe',
                'sha256': '0' * 64, 'size': 1}

    def run_manual_check(self, answer):
        service = Mock()
        service.events = queue.Queue()
        service.busy = False
        service.check.return_value = True
        with patch.object(companion, 'UPDATES', service), patch.object(companion, 'UPDATE_OFFER', False), \
                patch.object(companion, 'UPDATE_MANUAL', False), patch.object(companion, 'UPDATE_RETURN_MODE', 'awake'), \
                patch.object(companion, 'speech_busy', return_value=False), patch.object(companion, 'speak'):
            self.assertEqual(companion.handle('check for updates', 'document'), 'document')
            service.check.assert_called_once_with(manual=True)
            service.events.put(answer)
            mode = companion.poll_app_updates('document')
            if mode == 'update_offer':
                mode = companion.handle('no', 'update_offer')
            self.assertEqual(mode, 'document')
            # Later, while the user is editing, an automatic check finds an update.
            service.events.put(('available', self.release()))
            self.assertEqual(companion.poll_app_updates('document'), 'document')
            self.assertEqual(companion.poll_app_updates('awake'), 'update_offer')

    def test_up_to_date_answer_ends_manual_check(self):
        self.run_manual_check(('current', True))

    def test_failed_check_ends_manual_check(self):
        self.run_manual_check(('check_failed', True))

    def test_answered_manual_offer_ends_manual_check(self):
        self.run_manual_check(('available', self.release()))

    def test_unconfigured_answer_ends_manual_check(self):
        self.run_manual_check(('unconfigured', None))


class UnclosedHeadEmailTests(unittest.TestCase):
    def test_body_after_unclosed_head_is_read(self):
        from email_reader import render_html
        source = ('<html><head><meta charset="utf-8"><title>Newsletter</title>'
                  '<style>p{color:red}</style><body><p>Your claim was approved.</p>'
                  '<p>Call us at 555-0100.</p></body></html>')
        text = str(render_html(source))
        self.assertIn('Your claim was approved.', text)
        self.assertIn('Call us at 555-0100.', text)
        self.assertNotIn('Newsletter', text)
        self.assertNotIn('color', text)

    def test_text_directly_after_unclosed_head_is_read(self):
        from email_reader import render_html
        self.assertEqual(str(render_html('<head><title>Hidden</title>Plain body words')), 'Plain body words')

    def test_closed_head_still_hidden(self):
        from email_reader import render_html
        source = '<html><head><title>Hidden title</title><script>x=1</script></head><body><p>Hello</p></body></html>'
        self.assertEqual(str(render_html(source)), 'Hello')


class DraftFileTests(unittest.TestCase):
    def test_titles_differing_only_in_punctuation_get_separate_files(self):
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            drafts = Path(folder) / 'Email Drafts'
            first = VoiceEmail(drafts, title="Dad's party", recipient='first@example.com', subject='One')
            first.save()
            second = VoiceEmail(drafts, title='Dads party', recipient='second@example.com', subject='Two')
            second.save()
            self.assertNotEqual(first.path, second.path)
            self.assertNotEqual(first.state_path, second.state_path)
            self.assertEqual(len(list(drafts.glob('*.json'))), 2)
            self.assertEqual(len(list(drafts.glob('*.eml'))), 2)
            saved = {json.loads(p.read_text(encoding='utf-8'))['recipient'] for p in drafts.glob('*.json')}
            self.assertEqual(saved, {'first@example.com', 'second@example.com'})
            # Saving the first again still writes its own file.
            first.subject = 'One again'
            first.save()
            self.assertEqual(json.loads(second.state_path.read_text(encoding='utf-8'))['subject'], 'Two')

    def test_new_draft_never_replaces_a_draft_whose_message_file_is_missing(self):
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            drafts = Path(folder) / 'Email Drafts'
            older = VoiceEmail(drafts, recipient='keep@example.com')
            older.save()
            older.path.unlink()  # Interrupted save: state written, message file not yet.
            newer = VoiceEmail(drafts, recipient='new@example.com')
            newer.save()
            self.assertNotEqual(newer.state_path, older.state_path)
            self.assertEqual(json.loads(older.state_path.read_text(encoding='utf-8'))['recipient'], 'keep@example.com')

    def test_opened_draft_saves_to_the_same_file(self):
        from email_draft import VoiceEmail
        with tempfile.TemporaryDirectory() as folder:
            drafts = Path(folder) / 'Email Drafts'
            VoiceEmail(drafts, recipient='a@example.com').save()
            opened = VoiceEmail.open_existing(drafts, 'Untitled.')
            opened.subject = 'Changed'
            opened.save()
            self.assertEqual(sorted(p.name for p in drafts.glob('*.json')), ['Untitled.json'])


GMAIL_BASE = 'https://gmail.googleapis.com/gmail/v1/users/me'
GMAIL_BATCH = 'https://gmail.googleapis.com/batch/gmail/v1'


class FakeGmail:
    """Behaves like Gmail's REST API, including the multipart batch endpoint.

    Batch requests are multipart/mixed with one application/http part per call;
    the reply is multipart/mixed with parts named <response-ID>, not necessarily
    in request order. Gmail refuses more than 100 calls in one batch.
    """

    class Response:
        def __init__(self, body, content_type='application/json; charset=UTF-8', status=200):
            self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.offset = 0
            self.status = status
            self.headers = email.message.Message()
            self.headers['Content-Type'] = content_type
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, length=-1):
            end = len(self.body) if length is None or length < 0 else self.offset + length
            chunk = self.body[self.offset:end]
            self.offset += len(chunk)
            return chunk

    def __init__(self, total, missing=()):
        self.ids = ['m%04d' % n for n in range(total)]
        self.missing = set(missing)
        self.requests = []

    def metadata(self, ident):
        return {'id': ident, 'labelIds': ['INBOX'] + (['UNREAD'] if int(ident[1:]) % 2 else []),
                'sizeEstimate': 1000 + int(ident[1:]), 'internalDate': '1783036800000',
                'payload': {'headers': [{'name': 'From', 'value': ident + '@example.com'},
                                        {'name': 'Subject', 'value': 'Subject ' + ident}]}}

    def single(self, path):
        match = re.fullmatch(r'/gmail/v1/users/me/messages/([^?/]+)\?format=metadata'
                             r'&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date', path)
        if not match:
            return 400, {'error': {'code': 400, 'message': 'Bad request'}}
        if match[1] in self.missing or match[1] not in self.ids:
            return 404, {'error': {'code': 404, 'message': 'Not Found'}}
        return 200, self.metadata(match[1])

    def __call__(self, req, timeout):
        self.requests.append(req)
        if req.get_header('Authorization') != 'Bearer token':
            raise OSError('401 Unauthorized')
        url = req.full_url
        if url == GMAIL_BATCH:
            if req.get_method() != 'POST':
                raise OSError('405 Method Not Allowed')
            boundary = re.fullmatch(r'multipart/mixed; boundary=(\S+)', req.get_header('Content-type'))[1]
            pieces = req.data.decode().split('--' + boundary)
            assert pieces[-1].strip() == '--', 'batch body must end with the closing boundary'
            calls = []
            for piece in pieces[1:-1]:
                head, _, inner = piece.lstrip('\r\n').partition('\r\n\r\n')
                headers = dict(line.split(': ', 1) for line in head.split('\r\n'))
                assert headers['Content-Type'] == 'application/http'
                method, path = inner.strip().split('\r\n')[0].split(' ')[:2]
                assert method == 'GET'
                calls.append((headers['Content-ID'].strip('<>'), path))
            if len(calls) > 100:
                return self.Response({'error': {'code': 400, 'message': 'Too many requests in a batch.'}}, status=400)
            reply = 'batch_FakeReply'
            parts = []
            for content_id, path in reversed(calls):
                status, body = self.single(path)
                reason = 'OK' if status == 200 else 'Not Found' if status == 404 else 'Bad Request'
                parts.append('--' + reply + '\r\nContent-Type: application/http\r\nContent-ID: <response-' + content_id +
                             '>\r\n\r\nHTTP/1.1 ' + str(status) + ' ' + reason +
                             '\r\nContent-Type: application/json; charset=UTF-8\r\nVary: Origin\r\n\r\n' +
                             json.dumps(body, indent=2) + '\r\n')
            return self.Response((''.join(parts) + '--' + reply + '--\r\n').encode(),
                                 'multipart/mixed; boundary=' + reply)
        if url.startswith(GMAIL_BASE + '/messages?'):
            query = dict(part.split('=', 1) for part in url.split('?', 1)[1].split('&'))
            start = int(query.get('pageToken', '0'))
            size = int(query['maxResults'])
            page = self.ids[start:start + size]
            body = {'messages': [{'id': ident, 'threadId': 't' + ident} for ident in page]}
            if start + size < len(self.ids):
                body['nextPageToken'] = str(start + size)
            return self.Response(body)
        if url.startswith(GMAIL_BASE + '/messages/'):
            status, body = self.single(url[len('https://gmail.googleapis.com'):])
            if status != 200:
                raise OSError(str(status))
            return self.Response(body)
        raise OSError('404 unexpected ' + url)


class GmailBatchListingTests(unittest.TestCase):
    def setUp(self):
        import mailbox_access
        patcher = patch.object(mailbox_access, '_gmail_batch_paused_until', 0.0); patcher.start(); self.addCleanup(patcher.stop)

    def test_thousand_message_list_uses_batches_with_same_rows_and_order(self):
        from mailbox_access import MailboxClient
        from mail_voice import MailSession
        gmail = FakeGmail(1000)
        client = MailboxClient('gmail', 'token', gmail)
        with tempfile.TemporaryDirectory() as folder, patch('mail_voice.account_token', return_value=('me@example.com', 'token')):
            session = MailSession('gmail', folder, lambda *args: client)
            session.list_size = 1000
            session.folder_id, session.folder_name = 'INBOX', 'Inbox'
            session.list_messages()
        self.assertEqual([row['id'] for row in session.rows], gmail.ids)
        self.assertEqual(session.rows[1]['subject'], 'Subject m0001')
        self.assertEqual(session.rows[1]['from'], 'm0001@example.com')
        self.assertEqual(session.rows[1]['size'], 1001)
        self.assertTrue(session.rows[1]['unread'])
        self.assertFalse(session.rows[2]['unread'])
        self.assertIn('2026', session.rows[0]['date'])
        # Ten list pages and twenty batches of 50, instead of about a thousand requests.
        self.assertLessEqual(len(gmail.requests), 30)

    def test_batch_rows_match_single_request_rows(self):
        from mailbox_access import MailboxClient
        gmail = FakeGmail(7)
        rows, cursor = MailboxClient('gmail', 'token', gmail).list_messages('INBOX', limit=10)
        self.assertIsNone(cursor)
        single = MailboxClient('gmail', 'token', gmail)
        for row in rows:
            info = single._call('/messages/' + row['id'] + '?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date')
            self.assertEqual(row['subject'], info['payload']['headers'][1]['value'])
            self.assertEqual(row['size'], info['sizeEstimate'])
        self.assertEqual([r['id'] for r in rows], gmail.ids)

    def test_message_missing_inside_batch_reports_error_like_before(self):
        from mailbox_access import MailboxClient, MailboxError
        gmail = FakeGmail(5, missing={'m0003'})
        with self.assertRaises(MailboxError):
            MailboxClient('gmail', 'token', gmail).list_messages('INBOX')

    def batch_failing(self, gmail, error):
        original = gmail.__call__
        def opener(req, timeout):
            if req.full_url == GMAIL_BATCH: raise error
            return original(req, timeout)
        return opener

    def test_failed_batch_request_falls_back_to_one_message_at_a_time(self):
        # Before batching, listing fetched each message alone. If the batch
        # address fails for any reason, the inbox must still open the same way.
        from urllib.error import HTTPError
        from mailbox_access import MailboxClient
        for error in (OSError('connection reset'),
                      HTTPError(GMAIL_BATCH, 404, 'Not Found', {}, None),
                      HTTPError(GMAIL_BATCH, 400, 'Bad Request', {}, None)):
            with self.subTest(error=str(error)):
                gmail = FakeGmail(5)
                rows, _ = MailboxClient('gmail', 'token', self.batch_failing(gmail, error)).list_messages('INBOX')
                expected, _ = MailboxClient('gmail', 'token', FakeGmail(5)).list_messages('INBOX')
                self.assertEqual(rows, expected)

    def test_batch_is_not_retried_after_it_fails(self):
        from mailbox_access import MailboxClient
        gmail = FakeGmail(5); attempts = []
        original = gmail.__call__
        def opener(req, timeout):
            if req.full_url == GMAIL_BATCH: attempts.append(1); raise OSError('refused')
            return original(req, timeout)
        client = MailboxClient('gmail', 'token', opener)
        client.list_messages('INBOX'); client.list_messages('INBOX')
        self.assertEqual(len(attempts), 1)

    def test_batch_reply_that_is_not_multipart_falls_back(self):
        from mailbox_access import MailboxClient
        gmail = FakeGmail(5)
        original = gmail.__call__
        class Reply:
            headers = {'Content-Type': 'text/html'}
            def read(self, n=-1): return b'' if getattr(self, 'done', False) else setattr(self, 'done', True) or b'<html>error</html>'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def opener(req, timeout):
            if req.full_url == GMAIL_BATCH: return Reply()
            return original(req, timeout)
        rows, _ = MailboxClient('gmail', 'token', opener).list_messages('INBOX')
        self.assertEqual(len(rows), 5)


class GoogleContactsTests(unittest.TestCase):
    def test_one_bad_contact_does_not_stop_sync(self):
        from address_book import AddressBook
        from google_contacts import sync
        with tempfile.TemporaryDirectory() as tmp:
            class Selected:
                def __init__(self, folder): pass
                def selected(self): return ('gmail', 'me@example.com')
            book = AddressBook(tmp, 'me@example.com')
            book.save('Bad upload', 'bad@example.com')
            book.save('Good upload', 'good@example.com')
            remote = {'connections': [
                {'names': [{'displayName': 'Broken'}], 'emailAddresses': [{'value': None}]},
                {'names': 'not a list', 'emailAddresses': 'nope'},
                'not a person',
                {'names': [{'displayName': 'Google friend'}], 'emailAddresses': [{'value': 'google@example.com'}]}]}
            uploaded = []
            def api(url, token, data=None):
                if data is None: return remote
                if data['emailAddresses'][0]['value'] == 'bad@example.com':
                    raise OSError('HTTP Error 400: Bad Request')
                uploaded.append(data['emailAddresses'][0]['value']); return {}
            with patch('google_contacts._token', return_value='token'), patch('google_contacts.ProtectedStore', Selected), \
                    patch('google_contacts._api', side_effect=api):
                result = sync(tmp)
            self.assertIn('1 contacts added here; 1 local contacts added to Google', result)
            self.assertIn('1 contact could not be added to Google', result)
            self.assertEqual(uploaded, ['good@example.com'])
            self.assertEqual(book.get('Google friend')['address'], 'google@example.com')
            self.assertEqual(book.get('Bad upload')['address'], 'bad@example.com')


class RadioScheduleTests(unittest.TestCase):
    def test_damaged_entries_can_be_listed_and_canceled_by_voice(self):
        from media_hub import MediaHub
        with tempfile.TemporaryDirectory() as tmp:
            hub = MediaHub(tmp, notify=lambda text: None, player=Mock())
            jobs = ['junk', {'station': 'WGN', 'when': 5}, {'when': '2026-10-21T21:00:00', 'minutes': 5},
                    {'station': {'name': 'Later', 'url': 'https://r.example/l'}, 'when': '2026-10-22T09:00:00', 'minutes': 5}]
            hub._save('radio-schedule.json', jobs)
            spoken = hub.command('list scheduled recordings', section='radio')
            self.assertIn('1. A damaged schedule entry', spoken)
            self.assertIn('4. Later at 2026-10-22T09:00:00', spoken)
            self.assertEqual(hub.command('cancel scheduled recording 1', section='radio'),
                             'Canceled a damaged schedule entry.')
            self.assertEqual(hub.command('cancel scheduled recording 3', section='radio'),
                             'Canceled scheduled recording of Later.')
            self.assertEqual(len(hub._load('radio-schedule.json', [])), 2)

    def test_schedule_file_that_is_not_a_list_can_be_listed(self):
        from media_hub import MediaHub
        with tempfile.TemporaryDirectory() as tmp:
            hub = MediaHub(tmp, notify=lambda text: None, player=Mock())
            hub._save('radio-schedule.json', {'when': 'soon'})
            self.assertIn('damaged', hub.command('list scheduled recordings', section='radio'))
            self.assertEqual(hub.command('cancel scheduled recording 1', section='radio'), 'That schedule number is unavailable.')

    def test_malformed_scheduled_recording_is_reported_once_and_skipped(self):
        from media_hub import MediaHub
        with tempfile.TemporaryDirectory() as tmp:
            notices = []
            now = datetime(2026, 10, 21, 9, 0)
            hub = MediaHub(tmp, notify=notices.append, clock=lambda: now, player=Mock())
            good_time = (now + timedelta(hours=1)).isoformat(timespec='seconds')
            jobs = [{'station': {'name': 'Bad'}, 'when': 'not a time', 'minutes': 5},
                    {'station': 'WGN', 'when': now.isoformat(timespec='seconds'), 'minutes': 5},
                    {'when': now.isoformat(timespec='seconds'), 'minutes': 'ten', 'station': {'name': 'X', 'url': 'https://r.example/x'}},
                    {'station': {'name': 'Later', 'url': 'https://r.example/l'}, 'when': good_time, 'minutes': 5}]
            hub._save('radio-schedule.json', jobs)
            with patch.object(hub, 'refresh_iprd'), patch.object(hub, 'start_podcast_refresh'), \
                    patch.object(hub, 'record_now', return_value='Recording.') as record:
                for _ in range(3): hub.tick()
                self.assertEqual(len(notices), 1, notices)
                self.assertIn('scheduled radio recording', notices[0].lower())
                record.assert_not_called()
                # The good entry still records when it is due.
                now = now + timedelta(hours=1)
                hub.tick()
                record.assert_called_once_with({'name': 'Later', 'url': 'https://r.example/l'}, 5)
            self.assertEqual(len(hub._load('radio-schedule.json', [])), 3)

    def test_schedule_file_that_is_not_a_list_is_reported_once(self):
        from media_hub import MediaHub
        with tempfile.TemporaryDirectory() as tmp:
            notices = []
            hub = MediaHub(tmp, notify=notices.append, player=Mock())
            hub._save('radio-schedule.json', {'when': 'soon'})
            with patch.object(hub, 'refresh_iprd'), patch.object(hub, 'start_podcast_refresh'):
                for _ in range(3): hub.tick()
            self.assertEqual(len(notices), 1, notices)


class RadioSchedulerLoopTests(unittest.TestCase):
    def test_unexpected_scheduler_error_is_said_once(self):
        from media_hub import MediaHub
        with tempfile.TemporaryDirectory() as tmp:
            notices = []
            hub = MediaHub(tmp, notify=notices.append, player=Mock())
            ticks = []
            def broken_tick():
                ticks.append(1)
                if len(ticks) >= 4: raise SystemExit  # Ends the test thread.
                raise RuntimeError('unexpected')
            with patch.object(hub, 'tick', broken_tick), patch('media_hub.time.sleep'):
                hub.start_scheduler(); hub._scheduler.join(5)
            self.assertEqual(len(ticks), 4)
            self.assertEqual(len(notices), 1, notices)


class StoppedSpeechWorkerTests(unittest.TestCase):
    def test_progress_speech_never_raises_when_worker_stopped(self):
        from update_voice import ProgressSpeech
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ProgressSpeech, '_run', lambda self: None):
                speech = ProgressSpeech(folder)
            speech.thread.join(2)
            self.assertFalse(speech.thread.is_alive())
            for step in range(5, 1001, 5):
                speech.say(step, interrupt=False)
                speech.announce('Status button.')
            speech.finish()
            speech.close()
            self.assertLessEqual(speech.messages.qsize(), speech.messages.maxsize)

    def test_full_progress_queue_keeps_newest_message(self):
        from update_voice import ProgressSpeech
        with tempfile.TemporaryDirectory() as folder:
            release = threading.Event()
            with patch.object(ProgressSpeech, '_run', lambda self: release.wait(5)):
                speech = ProgressSpeech(folder)
            try:
                for step in range(1, 200):
                    speech.say(step, interrupt=False)
                speech.finish()
                items = []
                while not speech.messages.empty(): items.append(speech.messages.get_nowait())
                self.assertEqual(items[-1], ('complete', None, False))
            finally:
                release.set(); speech.close()


if __name__ == '__main__':
    unittest.main()

"""Release checks for the October 2026 launch review ("Should do before launch")."""
import json
import queue
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
    def test_common_real_world_email_reads_only_the_message(self):
        from email_reader import render_html
        samples = {
            '<div dir="ltr">Hi Will,<div>Lunch at noon?</div></div>': 'Hi Will,\nLunch at noon?',
            '<html><head><meta charset="utf-8"><title>News</title><style>p{}</style></head><body><p>Body text</p></body></html>': 'Body text',
            ('<html><head><meta name=Generator content="Microsoft Word 15"><!--[if gte mso 9]><xml><o:OfficeDocumentSettings>'
             '<o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]--><style>p{}</style></head>'
             '<body><p class=MsoNormal>Meeting moved to 3.</p></body></html>'): 'Meeting moved to 3.',
            ('<html><head><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml>'
             '<style>x{}</style></head><body><p>Real text</p></body></html>'): 'Real text',
            '<html><head><title>Sale</title></head><body><table><tr><td>Big sale today</td></tr></table></body></html>': 'Big sale today',
        }
        for source, expected in samples.items():
            with self.subTest(expected=expected):
                self.assertEqual(str(render_html(source)).strip(), expected)

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

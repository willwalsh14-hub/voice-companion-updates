"""Download manager: one list and queue for embedded Edge and the user's Chrome.

The fakes report in the browsers' own words: WebView2 DownloadOperation states
(InProgress, Interrupted with UserPaused, Completed) and chrome.downloads items
(in_progress, paused, interrupted with USER_CANCELED, complete). Controls are
recorded the way the real transports send them: without waiting for an answer.
"""
import io
import json
import os
import queue
import tempfile
import time
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import downloads
from downloads import DownloadManager, edge_report, chrome_report


class Controls:
    """Records requests as the WebView2 host and the extension receive them."""
    def __init__(self, fail=False):
        self.sent = []; self.folders = []; self.fail = fail
    def control(self, ref, action):
        if self.fail: raise OSError('Embedded browser closed.')
        self.sent.append((ref, action))
    def set_folder(self, folder): self.folders.append(folder)


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


def edge(ident, state='InProgress', reason='None', received=0, total=1000, new=False, path=None, can_resume=True, refused=False):
    """A Host.download message, as webview2_host.cs sends it."""
    return {'id': ident, 'new': new, 'path': path or 'C:\\Users\\Will\\Downloads\\' + ident + '.pdf', 'url': 'https://files.test/' + ident + '.pdf?token=secret',
            'state': state, 'reason': reason, 'received': received, 'total': total, 'canResume': can_resume, 'refused': refused}


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, self.folder, True)
        self.clock = Clock(); self.setting = ''
        self.model = DownloadManager(self.folder, folder_setting=lambda: self.setting, clock=self.clock, background=False)
        self.edge = Controls(); self.model.attach('edge', self.edge)
        self.chrome = Controls(); self.model.attach('chrome', self.chrome)

    def said(self):
        out = []
        while True:
            try: out.append(self.model.notices.get_nowait())
            except queue.Empty: return out

    def send(self, message):
        self.model.report('edge', message['id'], **edge_report(message))

    def record(self, ref): return self.model.find('edge', ref) or self.model.find('chrome', ref)

    def test_a_clicked_link_is_announced_with_progress_and_its_end(self):
        self.send(edge('report', new=True))
        self.assertEqual(self.said(), ['Downloading report.pdf.'])
        self.send(edge('report', received=450))
        self.assertEqual(self.model.labels(), ['report.pdf, 45 percent, downloading'])
        self.send(edge('report', 'Completed', received=1000))
        self.assertEqual(self.said(), ['report.pdf finished downloading.'])
        self.assertEqual(self.model.labels(), ['report.pdf, finished'])
        # The saved address never keeps a sign-in token.
        self.assertNotIn('secret', self.record('report').url)

    def test_unknown_size_reads_the_amount_so_far(self):
        self.send(edge('big', new=True, total=-1, received=3 * 1024 * 1024))
        self.assertEqual(self.model.labels(), ['big.pdf, 3 megabytes, downloading'])

    def test_three_run_at_once_and_the_rest_wait_in_line(self):
        for name in ('a', 'b', 'c', 'd', 'e'):
            self.send(edge(name, new=True))
        self.assertEqual(self.edge.sent, [('d', 'pause'), ('e', 'pause')])
        self.assertEqual(self.said()[-1], 'Downloading e.pdf. It is waiting in line.')
        labels = self.model.labels()
        self.assertEqual(labels[0], 'e.pdf, queued, number 2 in line')
        self.assertEqual(labels[1], 'd.pdf, queued, number 1 in line')
        # The browser is still busy pausing: an older "in progress" report is not news.
        self.send(edge('d', received=10))
        self.assertEqual(self.record('d').state, 'queued')
        self.send(edge('d', 'Interrupted', 'UserPaused'))
        self.assertEqual(self.record('d').state, 'queued')
        # One finishes: the first in line starts.
        self.send(edge('a', 'Completed', received=1000))
        self.assertEqual(self.edge.sent[-1], ('d', 'resume'))
        self.assertEqual(self.record('d').state, 'downloading')
        self.assertEqual(self.record('e').state, 'queued')

    def test_move_to_next_and_moving_up_and_down_in_line(self):
        for name in ('a', 'b', 'c', 'q1', 'q2', 'q3'):
            self.send(edge(name, new=True))
        q1, q2, q3 = self.record('q1'), self.record('q2'), self.record('q3')
        self.assertEqual(self.model.make_next(q3), 'q3.pdf is next in line.')
        self.assertEqual([r.ref for r in self.model.waiting()], ['q3', 'q1', 'q2'])
        self.assertEqual(self.model.make_next(q3), 'q3.pdf is already next in line.')
        self.assertEqual(self.model.move(q3, -1), 'q3.pdf is already first in line.')
        self.assertEqual(self.model.move(q2, 1), 'q2.pdf is already last in line.')
        self.assertEqual(self.model.move(q2, -1), 'q2.pdf moved up. It is number 2 in line.')
        self.assertEqual([r.ref for r in self.model.waiting()], ['q3', 'q2', 'q1'])
        self.assertEqual(self.model.move(q3, 1), 'q3.pdf moved down. It is number 2 in line.')
        self.assertTrue(self.model.move(self.record('a'), 1).startswith('Only a queued download can move in line. a.pdf, 0 percent, downloading'))
        # The queue order survives a restart for Chrome, which keeps its paused downloads.
        self.send(edge('b', 'Completed', received=1000))
        self.assertEqual(self.edge.sent[-1], ('q2', 'resume'))

    def test_space_pauses_and_resumes_and_a_pause_frees_a_place(self):
        for name in ('a', 'b', 'c', 'd'):
            self.send(edge(name, new=True))
        a = self.record('a')
        self.assertEqual(self.model.toggle(a), 'Paused a.pdf.')
        self.assertEqual(self.edge.sent[-2:], [('a', 'pause'), ('d', 'resume')])
        self.assertEqual(self.model.labels()[-1], 'a.pdf, 0 percent, paused')
        # Three are running again, so a resumed download waits its turn.
        self.assertEqual(self.model.toggle(a), 'a.pdf is queued. It starts when another download finishes. It is number 1 in line.')
        self.send(edge('b', 'Completed'))
        self.assertEqual(self.edge.sent[-1], ('a', 'resume'))
        self.assertEqual(self.model.toggle(self.record('b')), 'b.pdf is finished. Press Enter to open it.')
        # A queued download can be held back; it does not start by itself.
        self.send(edge('e', new=True))
        e = self.record('e')
        self.assertEqual(self.model.toggle(e), 'Paused e.pdf. It will not start until you resume it.')
        self.send(edge('c', 'Completed'))
        self.assertNotIn(('e', 'resume'), self.edge.sent)
        self.assertEqual(self.model.toggle(e), 'Resuming e.pdf.')

    def test_cancel_and_remove(self):
        self.send(edge('a', new=True)); self.said()
        a = self.record('a')
        self.assertEqual(self.model.cancel(a), 'Cancelled a.pdf.')
        self.assertEqual(self.edge.sent[-1], ('a', 'cancel'))
        self.send(edge('a', 'Interrupted', 'UserCanceled', can_resume=False))
        self.assertEqual(self.said(), [])   # the user already heard "Cancelled"
        self.assertEqual(self.model.labels(), ['a.pdf, cancelled'])
        self.assertEqual(self.model.remove(a), 'Removed a.pdf from the list.')
        self.assertEqual(self.model.items, [])
        self.send(edge('b', new=True)); self.send(edge('b', 'Completed'))
        self.assertEqual(self.model.remove(self.record('b')), 'Removed b.pdf from the list. The file is still in its folder.')

    def test_failures_say_why_and_what_to_do(self):
        self.send(edge('a', new=True)); self.said()
        self.send(edge('a', 'Interrupted', 'NetworkDisconnected', received=300))
        self.assertEqual(self.said(), ['a.pdf did not finish downloading. The internet connection was lost. To try again, open Downloads and press Space.'])
        self.assertEqual(self.model.toggle(self.record('a')), 'Trying a.pdf again.')
        self.assertEqual(self.edge.sent[-1], ('a', 'resume'))
        self.send(edge('b', new=True)); self.said()
        self.send(edge('b', 'Interrupted', 'FileMalicious', can_resume=False))
        self.assertIn('blocked this file as unsafe. Go back to the web page and choose the link again.', self.said()[0])
        self.assertEqual(self.model.toggle(self.record('b')), 'b.pdf cannot continue. Go back to the web page and choose the link again.')

    def test_a_refused_request_is_reported_at_once(self):
        self.send(edge('a', new=True)); self.said()
        self.model.toggle(self.record('a'))
        self.send(edge('a', received=5, refused=True))
        self.assertEqual(self.record('a').state, 'downloading')
        self.assertEqual(self.said(), ['Embedded Edge could not change a.pdf. It is 0 percent, downloading.'])

    def test_closing_embedded_edge_stops_its_downloads_and_says_so(self):
        self.send(edge('a', new=True)); self.said()
        self.model.detach('edge', self.edge)
        self.assertEqual(self.record('a').state, 'failed')
        self.assertEqual(self.said(), ['a.pdf stopped because Embedded Edge closed. Open the page again and choose the link to download it.'])
        self.assertEqual(self.model.toggle(self.record('a')), 'a.pdf cannot continue. Go back to the web page and choose the link again.')
        # Changing a download whose browser is gone says what to do.
        self.send(edge('b', new=True)); self.model.detach('chrome', self.chrome, stopped=False)
        chrome = {'id': 4, 'filename': 'C:\\d\\c.pdf', 'state': 'in_progress', 'new': True}
        self.model.attach('chrome', self.chrome); self.model.report('chrome', 4, **chrome_report(chrome))
        self.model.detach('chrome', self.chrome, stopped=False)
        with self.assertRaisesRegex(ValueError, 'Chrome is not connected right now, so c.pdf cannot be changed. Open Chrome'):
            self.model.toggle(self.model.find('chrome', '4'))

    def test_history_persists_newest_first_and_unfinished_ones_are_stopped(self):
        self.send(edge('old', new=True)); self.send(edge('old', 'Completed'))
        self.clock.now += 1
        self.send(edge('running', new=True, received=200))
        data = json.loads((self.folder / 'download-history.json').read_text(encoding='utf-8'))
        self.assertEqual([d['ref'] for d in data], ['running', 'old'])
        again = DownloadManager(self.folder, background=False)
        self.assertEqual(again.labels(), ['running.pdf, failed', 'old.pdf, finished'])
        self.assertIn('closed before it finished', again.details(again.items[0]))
        # A damaged history file is kept aside and the list starts fresh.
        (self.folder / 'download-history.json').write_text('{not json', encoding='utf-8')
        fresh = DownloadManager(self.folder, background=False)
        self.assertEqual(fresh.items, [])
        self.assertTrue((self.folder / 'download-history.json.damaged').is_file())

    def test_chrome_names_the_file_later_and_keeps_going_while_companion_is_closed(self):
        item = {'id': 7, 'filename': '', 'url': 'https://files.test/get?id=7', 'state': 'in_progress', 'paused': False,
                'canResume': True, 'received': 0, 'total': 0, 'danger': 'safe', 'new': True}
        self.model.report('chrome', 7, **chrome_report(item))
        self.assertEqual(self.said(), [])   # not "Downloading get"
        item.update(filename='C:\\Users\\Will\\Downloads\\taxes.zip', new=False, received=10)
        self.model.report('chrome', 7, **chrome_report(item))
        self.assertEqual(self.said(), ['Downloading taxes.zip.'])
        again = DownloadManager(self.folder, background=False)
        chrome = Controls(); again.attach('chrome', chrome)
        self.assertEqual(again.labels(), ['taxes.zip, failed'])
        # On relinking, Chrome reports it still running, then done.
        again.report('chrome', 7, **chrome_report(dict(item, received=20)))
        self.assertEqual(again.labels(), ['taxes.zip, 20 bytes, downloading'])
        again.report('chrome', 7, **chrome_report(dict(item, state='complete', received=40)))
        self.assertEqual(again.notices.get_nowait(), 'taxes.zip finished downloading.')
        # Chrome's older downloads that Companion never saw are not added.
        again.report('chrome', 99, **chrome_report(dict(item, state='complete')))
        self.assertIsNone(again.find('chrome', '99'))

    def test_chrome_files_move_to_the_folder_chosen_in_options(self):
        chrome_folder = self.folder / 'Chrome'; chosen = self.folder / 'Chosen'
        chrome_folder.mkdir(); chosen.mkdir()
        (chosen / 'notes.txt').write_text('older file', encoding='utf-8')
        source = chrome_folder / 'notes.txt'; source.write_text('new file', encoding='utf-8')
        item = {'id': 3, 'filename': str(source), 'url': 'https://files.test/notes.txt', 'state': 'in_progress', 'new': True, 'total': 8}
        self.model.report('chrome', 3, **chrome_report(item)); self.said()
        self.setting = str(chosen)
        self.model.report('chrome', 3, **chrome_report(dict(item, state='complete', received=8)))
        self.assertEqual(self.said(), ['notes (1).txt finished downloading.'])
        self.assertEqual((chosen / 'notes (1).txt').read_text(encoding='utf-8'), 'new file')
        self.assertEqual((chosen / 'notes.txt').read_text(encoding='utf-8'), 'older file')
        self.assertFalse(source.exists())
        self.assertEqual(self.record('3').path, str(chosen / 'notes (1).txt'))

    def test_chrome_dangerous_files_wait_for_an_answer_in_chrome(self):
        item = {'id': 5, 'filename': 'C:\\d\\setup.exe', 'state': 'in_progress', 'danger': 'file', 'new': True}
        self.model.report('chrome', 5, **chrome_report(item))
        self.assertEqual(self.model.labels(), ['setup.exe, waiting for your answer in Chrome'])

    def test_embedded_edge_learns_the_download_folder(self):
        self.assertEqual(self.edge.folders[-1], str(downloads.default_folder()))
        chosen = self.folder / 'Mine'; chosen.mkdir(); self.setting = str(chosen)
        self.model.push_folder()
        self.assertEqual(self.edge.folders[-1], str(chosen))

    def test_browser_words_map_to_one_set_of_states(self):
        self.assertEqual(edge_report(edge('x', 'Interrupted', 'UserPaused'))['state'], 'paused')
        self.assertEqual(edge_report(edge('x', 'Interrupted', 'ServerNoRange'))['reason'], 'server')
        self.assertEqual(edge_report(edge('x', 'Interrupted', 'UserShutdown'))['reason'], 'closed')
        self.assertEqual(chrome_report({'state': 'interrupted', 'error': 'NETWORK_FAILED'})['reason'], 'network')
        self.assertEqual(chrome_report({'state': 'interrupted', 'error': 'USER_CANCELED'})['state'], 'cancelled')
        self.assertEqual(chrome_report({'state': 'in_progress', 'paused': True})['state'], 'paused')
        self.assertIsNone(chrome_report({'state': 'in_progress', 'total': -1})['total'])


class FolderTests(unittest.TestCase):
    def test_folder_setting_accepts_the_default_names_and_real_folders(self):
        for value in ('', '  ', 'Downloads', 'Downloads.', 'my downloads folder', 'default'):
            self.assertEqual(downloads.folder_setting(value), '', value)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(downloads.folder_setting(folder + '.'), folder)
            with self.assertRaisesRegex(ValueError, 'does not exist'):
                downloads.folder_setting(os.path.join(folder, 'missing'))
        with self.assertRaisesRegex(ValueError, 'full folder path'):
            downloads.folder_setting('music')
        self.assertEqual(downloads.spoken_path('C colon backslash Users backslash Will.'), 'C:\\Users\\Will')
        self.assertEqual(downloads.spoken_path('"D:/Saved"'), 'D:/Saved')

    @unittest.skipIf(os.name == 'nt' or hasattr(os, 'geteuid') and os.geteuid() == 0, 'needs a folder this account cannot write')
    def test_a_folder_without_write_access_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o500)
            try:
                with self.assertRaisesRegex(ValueError, 'cannot save files'):
                    downloads.folder_setting(folder)
            finally:
                os.chmod(folder, 0o700)

    def test_settings_check_the_folder_only_when_it_changes(self):
        from settings_model import SettingsSession
        with tempfile.TemporaryDirectory() as folder:
            session = SettingsSession({'download_folder': '/gone/away'}, {})
            session.set('download_folder', '/gone/away')   # unchanged: other options still save
            with self.assertRaises(ValueError):
                session.set('download_folder', '/also/missing')
            session.set('download_folder', folder)
            self.assertEqual(session.values['download_folder'], folder)
            session.set('download_folder', 'Downloads')
            self.assertEqual(session.values['download_folder'], '')
            self.assertEqual(session.voice_setting('set download folder to ' + folder), ('download_folder', folder))

    def test_unique_names_and_moves(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'a.txt').write_text('1'); (root / 'a (1).txt').write_text('2')
            self.assertEqual(downloads.unique_path(root, 'a.txt').name, 'a (2).txt')
            other = root / 'other'; other.mkdir(); (other / 'a.txt').write_text('3')
            moved = downloads.move_into(other / 'a.txt', root)
            self.assertEqual(moved.name, 'a (2).txt'); self.assertEqual(moved.read_text(), '3')


class TransportTests(unittest.TestCase):
    """The real transports pass download news to the model, not to the page driver."""
    def setUp(self):
        self.folder = tempfile.mkdtemp(); self.addCleanup(__import__('shutil').rmtree, self.folder, True)
        self.model = downloads.manager(self.folder)
        self.model.background = False

    def test_embedded_edge_pipe(self):
        from webview2_backend import WebViewPipe, WebView2Backend
        pipe = object.__new__(WebViewPipe)
        lines = [json.dumps({'method': 'Host.download', 'params': edge('a', new=True)}) + '\n',
                 json.dumps({'method': 'Page.loadEventFired', 'params': {}}) + '\n']
        pipe.process = Mock(stdout=io.StringIO(''.join(lines)))
        pipe.closed = __import__('threading').Event(); pipe.pending_lock = __import__('threading').Lock(); pipe.pending = {}
        pipe.page_order = []; pipe.own_replies = {}; pipe.own_serial = -1000; pipe.ready = Mock(); pipe.error = None
        forwarded = []; pipe.connection = Mock(send=forwarded.append); pipe.send_lock = __import__('threading').Lock()
        backend = object.__new__(WebView2Backend)
        sent = []; pipe._send = sent.append
        backend._link_downloads(pipe)
        self.assertEqual(sent[-1]['method'], 'Host.downloadFolder')
        reports = []; pipe.on_download = lambda params, report=pipe.on_download: (report(params), reports.append(self.model.labels()))
        pipe._read()
        self.assertEqual([json.loads(x)['method'] for x in forwarded], ['Page.loadEventFired'])
        self.assertEqual(reports, [['a.pdf, 0 percent, downloading']])
        # The host closed on its own: its download stopped, and the user hears it.
        self.assertEqual(self.model.labels(), ['a.pdf, failed'])
        self.assertIn('a.pdf stopped because Embedded Edge closed.', list(self.model.notices.queue)[-1])
        self.model.attach('edge', downloads.EdgeDownloads(pipe))
        self.model.report('edge', 'b', **edge_report(edge('b', new=True)))
        self.model.toggle(self.model.find('edge', 'b'))
        self.assertEqual(sent[-1]['method'], 'Host.downloadControl')
        self.assertEqual(sent[-1]['params'], {'id': 'b', 'action': 'pause'})
        self.assertLess(sent[-1]['id'], 0)   # Companion's own request; never waited on

    def test_chrome_link(self):
        from browser_cdp_relay import BrowserRelay
        relay = object.__new__(BrowserRelay)
        import threading
        relay.state_lock = threading.Lock(); relay.pipe_lock = threading.Lock(); relay.connected = threading.Event()
        relay.generation = 0; relay.browser = 'chrome'
        forwarded = []; relay.websocket = Mock(send=forwarded.append)
        item = {'id': 9, 'filename': 'C:\\Users\\Will\\Downloads\\song.mp3', 'state': 'in_progress', 'new': True, 'total': 100}
        messages = [{'method': 'VoiceCompanion.ready', 'browser': 'chrome'},
                    {'method': 'VoiceCompanion.download', 'params': item},
                    {'method': 'VoiceCompanion.download', 'params': 'not a download'},
                    {'id': 1, 'result': {}}]
        written = []
        class Pipe:
            def recv_bytes(self, limit):
                if not messages: raise EOFError
                return json.dumps(messages.pop(0)).encode()
            def send_bytes(self, data): written.append(json.loads(data))
            def close(self): pass
        relay.pipe = pipe = Pipe()
        relay._read_pipe(pipe)
        self.assertEqual([json.loads(x) for x in forwarded], [{'id': 1, 'result': {}}])
        self.assertEqual(self.model.labels(), ['song.mp3, 0 percent, downloading'])
        # The link went down: Chrome keeps the download, and so does the list.
        self.assertEqual(self.model.items[0].state, 'downloading')
        relay.pipe = pipe; self.model.attach('chrome', relay.downloads)
        self.model.cancel(self.model.items[0])
        self.assertEqual(written[-1], {'method': 'VoiceCompanion.downloadControl', 'params': {'id': '9', 'action': 'cancel'}})

    def test_an_address_that_is_a_file_says_so(self):
        from web_assistant import BrowserBackend, WebError
        backend = object.__new__(BrowserBackend)
        backend.start = Mock(); backend.page = Mock()
        backend.page.goto.side_effect = Exception('page.goto: Download is starting')
        with self.assertRaisesRegex(WebError, 'is a file, so it is downloading'):
            backend.goto('https://files.test/a.zip')

    def test_leaving_the_page_keeps_embedded_edge_open_until_its_downloads_end(self):
        from web_assistant import WebSession
        session = object.__new__(WebSession)
        session.backend = Mock(browser='webview2'); session.reading = Mock()
        self.model.attach('edge', Controls())
        self.model.report('edge', 'a', **edge_report(edge('a', new=True)))
        session.leave(False)
        session.backend.close.assert_not_called()
        self.assertTrue(session.close_after_downloads)
        import download_screen
        app = Mock(web_session=session, SLEEP_RETURN_MODE='awake', PREFERENCES={}, APP=Path(self.folder))
        download_screen.poll(app, 'browser_menu')
        session.backend.close.assert_not_called()
        self.model.report('edge', 'a', **edge_report(edge('a', 'Completed')))
        download_screen.poll(app, 'browser_menu')
        session.backend.close.assert_called_once()
        app.speak.assert_any_call('a.pdf finished downloading.')


class ScreenTests(unittest.TestCase):
    """The Downloads screen through companion, by keys and by voice."""
    def setUp(self):
        import companion as app
        from field_selection import FieldSelection
        from reading_navigation import ReadingCursor
        import download_screen
        self.app = app; self.screen = download_screen
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.web = Mock(snapshot={'url': 'https://example.com'}, pending=None, submit_pending=None, list_focus=None,
                        keyboard_field=None, form_index=None, close_after_downloads=False)
        self.web.reading.position = 42
        defaults = {'APP': self.root, 'TEXT_MODE': True, 'APP_WINDOW': None, 'INPUT_MODE': 'mixed', 'document': None,
                    'email_draft': None, 'mail_session': None, 'web_session': self.web, 'pending_website': None,
                    'pending_send': None, 'NAMED_LIST': None, 'account_setup': False, 'MAIN_MENU_INDEX': None,
                    'SYNTH_PICK_INDEX': None, 'VOICE_PICK_INDEX': None, 'help_session': None, 'tutorial_session': None,
                    'MEDIA_HUB': None, 'PREFERENCES': dict(app.PREFERENCES), 'SLEEP_RETURN_MODE': 'awake',
                    'FILE_READER': None, 'FILE_MANAGER': None, 'TASK_NAV': None, 'SCREEN_MENU': None, 'ACCOUNT_PICKER': None,
                    'ACCOUNT_ENTRY': None, 'THUNDERBIRD_SETUP': None, 'CONFIRM_CHOICE': 'yes'}
        for name, value in defaults.items():
            self.stack.enter_context(patch.object(app, name, value, create=True))
        self.said = []
        speak = lambda text, *a, **k: self.said.append(str(text))
        for name in ('speak', 'speak_prompt', 'dictation_feedback', 'speak_status'):
            self.stack.enter_context(patch.object(app, name, side_effect=speak))
        self.stack.enter_context(patch.object(app, 'interrupt_speech'))
        for name, value in (('RETURN_MODE', 'web'), ('INDEX', 0), ('SELECTED', None), ('PENDING', None), ('CHOICE', 'no'), ('OPENED', None)):
            self.stack.enter_context(patch.object(download_screen, name, value))
        self.model = download_screen.manager(app)
        self.model.background = False
        self.edge = Controls(); self.model.attach('edge', self.edge)

    def add(self, name, state='InProgress', reason='None', received=0, total=1000):
        path = self.root / name
        if state == 'Completed':
            path.write_text('Hello from ' + name + '.', encoding='utf-8')
        self.model.report('edge', name, **edge_report(edge(name, 'InProgress', new=True, path=str(path))))
        self.model.report('edge', name, **edge_report(edge(name, state, reason, received=received, total=total, path=str(path))))
        while not self.model.notices.empty(): self.model.notices.get_nowait()
        self.app.APP_WINDOW = None

    def press(self, key, mode):
        request = self.app.navigation_key(key, mode)
        return self.app.handle_keyboard(request, mode) if request else mode

    def test_control_d_opens_downloads_and_escape_returns_to_the_same_place_on_the_page(self):
        self.add('report.pdf', received=450)
        self.add('song.mp3', 'Completed')
        from input_parity import screen_key
        self.assertEqual(screen_key('web', 'd', 4, {}), 'Ctrl+D')
        mode = self.press('Ctrl+D', 'web')
        self.assertEqual(mode, 'downloads')
        self.assertEqual(self.said[-1], 'Downloads. song.mp3, finished, 1 of 2.')
        self.assertEqual(self.press('Down', mode), 'downloads')
        self.assertEqual(self.said[-1], 'report.pdf, 45 percent, downloading, 2 of 2.')
        self.press('Down', mode)
        self.assertEqual(self.said[-1], 'End of list. report.pdf, 45 percent, downloading, 2 of 2.')
        self.press('Home', mode); self.press('Up', mode)
        self.assertEqual(self.said[-1], 'Start of list. song.mp3, finished, 1 of 2.')
        self.assertEqual(self.press('Escape', mode), 'web')
        self.assertEqual(self.said[-1], 'Back to web page.')
        self.assertEqual(self.web.reading.position, 42)
        self.web.leave.assert_not_called(); self.web.command.assert_not_called()

    def test_voice_opens_it_from_the_web_and_the_browser_menu_but_never_in_writing(self):
        self.assertEqual(self.app.handle('Download manager.', 'web'), 'downloads')
        self.assertEqual(self.said[-1], 'Downloads. No downloads yet. Files you download are saved in ' + downloads.default_folder().name + '. Escape goes back.')
        from browser_menu import BrowserMenu
        self.app.web_session = self.web; self.web.menu = BrowserMenu(self.root)
        self.assertIn(('Downloads', 'downloads'), self.web.menu.choices(self.web, False))
        self.web.menu.index = [x[1] for x in self.web.menu.choices(self.web, False)].index('downloads')
        self.assertEqual(self.app.handle('enter', 'browser_menu'), 'downloads')
        self.assertEqual(self.app.handle('close downloads', 'downloads'), 'browser_menu')
        self.assertEqual(self.app.handle('downloads', 'browser_menu'), 'downloads')
        self.assertIn('Downloads', self.web.menu.start())
        # A favorite named Downloads is a name, not a command.
        self.web.menu.level = 'name'
        self.assertIsNone(self.screen.handle(self.app, 'downloads', 'browser_menu'))
        # On a web page, "Downloads" alone may be a link on the site.
        self.assertIsNone(self.screen.handle(self.app, 'downloads', 'web'))
        # In a note, the words are text.
        self.assertIsNone(self.screen.handle(self.app, 'download manager', 'note'))
        self.assertEqual(self.screen.handle(self.app, 'download manager', 'note', typed=True), 'downloads')

    def test_space_delete_and_control_arrows_match_their_voice_commands(self):
        for name in ('a.pdf', 'b.pdf', 'c.pdf', 'd.pdf', 'e.pdf'):
            self.add(name)
        mode = self.app.handle('download manager', 'web')
        # Newest first: e and d wait in line.
        self.assertEqual(self.said[-1], 'Downloads. e.pdf, queued, number 2 in line, 1 of 5.')
        self.press('Ctrl+Up', mode)
        self.assertEqual(self.said[-1], 'e.pdf moved up. It is number 1 in line.')
        self.app.handle('move download later', mode)
        self.assertEqual(self.said[-1], 'e.pdf moved down. It is number 2 in line.')
        self.app.handle('Move to next.', mode)
        self.assertEqual(self.said[-1], 'e.pdf is next in line.')
        self.press('Down', mode); self.press('Down', mode)   # c.pdf, running
        self.press('Space', mode)
        self.assertEqual(self.said[-1], 'Paused c.pdf.')
        self.assertEqual(self.edge.sent[-1], ('e.pdf', 'resume'))
        self.app.handle('resume download', mode)
        self.assertEqual(self.said[-1], 'c.pdf is queued. It starts when another download finishes. It is number 2 in line.')
        # Delete asks first. No is the first choice, as in File manager.
        self.press('Down', mode)
        self.press('Delete', mode)
        self.assertEqual(self.said[-1], 'Cancel b.pdf? The part already downloaded is deleted. Say Yes or No. Escape keeps it downloading.')
        self.assertEqual(self.app.app_context('downloads')['confirmation'], 'no')
        self.press('Enter', mode)
        self.assertTrue(self.said[-1].startswith('Not cancelled. b.pdf, 0 percent, downloading'))
        self.press('Delete', mode); self.press('Up', mode)
        self.assertEqual(self.said[-1], 'Yes.')
        self.press('Enter', mode)
        self.assertEqual(self.said[-1], 'Cancelled b.pdf.')
        self.assertIn(('b.pdf', 'cancel'), self.edge.sent)
        self.app.handle('cancel download', mode)
        self.assertEqual(self.said[-1], 'b.pdf, cancelled. Nothing to cancel. Press Delete to take it off the list.')
        # Delete on a download that ended takes it off the list.
        self.press('Delete', mode)
        self.assertTrue(self.said[-1].startswith('Removed b.pdf from the list.'))
        self.assertNotIn('b.pdf', ' '.join(self.model.labels()))

    def test_enter_opens_files_in_companion_and_comes_back(self):
        self.add('notes.txt', 'Completed'); self.add('song.mp3', 'Completed')
        self.add('setup.exe', 'Completed'); self.add('photos.zip', 'Completed')
        mode = self.app.handle('download manager', 'web')
        started = []
        with patch('os.startfile', side_effect=started.append, create=True):
            # Another type opens in its Windows app.
            self.press('Enter', mode)
            self.assertEqual(started, [str(self.root / 'photos.zip')])
            self.assertTrue(self.said[-1].startswith('Opening photos.zip in its Windows app.'))
            # A program asks first.
            self.press('Down', mode); self.press('Enter', mode)
            self.assertEqual(self.said[-1], 'setup.exe is a program. Run it? Only run programs from websites you trust. Say Yes or No.')
            self.press('n', mode)
            self.assertEqual(len(started), 1)
            self.press('Enter', mode); self.press('y', mode)
            self.assertEqual(started[-1], str(self.root / 'setup.exe'))
        # Sound and video play in the Voice Companion player; Escape comes back here.
        hub = Mock()
        with patch.object(self.app, 'media', return_value=hub):
            self.press('Down', mode)
            mode = self.press('Enter', 'downloads')
            self.assertEqual(mode, 'local_media')
            hub.player.open.assert_called_once()
            self.assertEqual(self.app.handle('escape', mode), 'downloads')
        self.assertEqual(self.said[-1], 'Downloads. song.mp3, finished, 3 of 4.')
        # Documents open in the document reader; Escape comes back here.
        mode = self.press('Down', 'downloads')
        mode = self.press('Enter', mode)
        self.assertEqual(mode, 'files')
        until = time.monotonic() + 5
        while self.app.FILE_MANAGER.events.empty() and time.monotonic() < until: time.sleep(.01)
        mode = self.app.poll_file_manager(mode)
        self.assertEqual(mode, 'file_reader')
        self.assertIn('Hello from notes.txt', ' '.join(self.said))
        self.assertEqual(self.press('Escape', mode), 'downloads')
        self.assertEqual(self.said[-1], 'Downloads. notes.txt, finished, 4 of 4.')
        # Later, a document opened another way returns to File manager as usual.
        from document_reader import open_document
        from file_manager import FileManager
        other = self.root / 'letter.txt'; other.write_text('Dear Sam.', encoding='utf-8')
        self.app.task_navigation().reader_return = 'downloads'   # left over, as after a Word file
        self.app.FILE_MANAGER = FileManager(); self.app.FILE_READER = open_document(other)
        with patch.object(self.app.FILE_MANAGER, 'say_current', return_value='letter.txt.'):
            self.assertEqual(self.app.handle('back to files', 'file_reader'), 'files')
        self.assertEqual(self.said[-1], 'Back to file manager. letter.txt.')
        # A file deleted since is not opened.
        (self.root / 'notes.txt').unlink()
        self.press('Enter', 'downloads')
        self.assertEqual(self.said[-1], 'notes.txt is no longer in its folder. It may have been moved or deleted.')

    def test_running_downloads_read_their_details_on_enter_and_help_is_f1(self):
        self.add('big.iso', received=512 * 1024, total=1024 * 1024)
        mode = self.app.handle('download manager', 'web')
        self.press('Enter', mode)
        self.assertEqual(self.said[-1], 'big.iso, 50 percent, downloading. 512 kilobytes of 1 megabytes. Folder: '
                         + self.root.name + '. From files.test. Embedded Edge. Space pauses or resumes it.')
        self.assertEqual(self.app.navigation_key('F1', mode), 'help with downloads')
        self.assertEqual(self.app.handle('help with downloads', mode), 'help')
        self.assertEqual(self.app.help_session.topics[self.app.help_session.topic_index]['name'], 'Download Files')

    def test_letters_jump_and_unknown_words_get_a_hint(self):
        self.add('alpha.pdf'); self.add('bravo.pdf')
        mode = self.app.handle('download manager', 'web')
        self.assertTrue(self.app.app_context(mode)['menu_letters'])
        self.press('Letter:a', mode)
        self.assertEqual(self.said[-1], 'alpha.pdf, 0 percent, downloading, 2 of 2.')
        self.app.handle('bravo', mode)
        self.assertEqual(self.said[-1], 'bravo.pdf, 0 percent, downloading, 1 of 2.')
        self.app.handle('zebra quantum lantern', mode)
        self.assertTrue(self.said[-1].startswith('Downloads. Say pause download'))
        self.assertEqual(self.app.handle('main menu', mode), 'awake')

    def test_a_new_download_does_not_move_you_off_your_place(self):
        self.add('first.pdf'); self.add('second.pdf')
        mode = self.app.handle('download manager', 'web')
        self.press('Down', mode)
        self.assertEqual(self.said[-1], 'first.pdf, 0 percent, downloading, 2 of 2.')
        self.add('third.pdf')
        self.app.handle('download details', mode)
        self.assertTrue(self.said[-1].startswith('first.pdf, 0 percent, downloading.'))
        self.press('Up', mode)
        self.assertEqual(self.said[-1], 'second.pdf, 0 percent, downloading, 2 of 3.')

    def test_news_is_spoken_on_any_screen(self):
        self.model.report('edge', 'x', **edge_report(edge('x', new=True)))
        self.screen.poll(self.app, 'awake')
        self.assertEqual(self.said[-1], 'Downloading x.pdf.')


if __name__ == '__main__':
    unittest.main()

"""Web, download and media defects found in the October 2026 review.

Each test failed before its fix. They use real files and the app's own
command paths; fakes behave like the browser, player or feed they stand for.
"""
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import downloads
import test_downloads
from downloads import edge_report


class DownloadScreenTests(unittest.TestCase):
    """The Downloads screen, through companion, as test_downloads.ScreenTests sets it up."""
    setUp = test_downloads.ScreenTests.setUp
    add = test_downloads.ScreenTests.add
    press = test_downloads.ScreenTests.press

    def test_cancel_it_never_runs_a_program_and_run_it_never_cancels_a_download(self):
        self.add('setup.exe', 'Completed')
        mode = self.app.handle('download manager', 'web')
        started = []
        with patch('os.startfile', side_effect=started.append, create=True):
            self.press('Enter', mode)
            self.assertIn('is a program. Run it?', self.said[-1])
            # "Cancel it" is a No to "Run it?".
            self.app.handle('cancel it', mode)
            self.assertEqual(started, [])
            self.assertTrue(self.said[-1].startswith('Not opened.'), self.said[-1])
            self.press('Enter', mode)
            self.app.handle('Run it.', mode)
            self.assertEqual(started, [str(self.root / 'setup.exe')])
        # "Run it" is not a Yes to "Cancel this download?".
        self.add('movie.mp4')
        self.press('Home', mode)
        self.press('Delete', mode)
        self.assertIn('Cancel movie.mp4?', self.said[-1])
        self.app.handle('run it', mode)
        self.assertNotIn(('movie.mp4', 'cancel'), self.edge.sent)
        self.app.handle('Cancel it.', mode)
        self.assertIn(('movie.mp4', 'cancel'), self.edge.sent)


    def test_enter_on_every_kind_of_unfinished_download_says_what_to_do_next(self):
        self.add('slow.iso', received=100, total=1000)
        self.add('lost.zip', 'Interrupted', 'NetworkFailed', received=100)
        self.add('gone.pdf', 'Interrupted', 'ServerBadContent', received=100)
        self.model.find('edge', 'gone.pdf').can_resume = False
        self.add('stopped.mp3', 'Interrupted', 'UserCanceled')
        self.model.report('chrome', 'risky', new=True, state='blocked', path=str(self.root / 'risky.exe'))
        while not self.model.notices.empty(): self.model.notices.get_nowait()
        mode = self.app.handle('download manager', 'web')
        heard = {}
        for _ in range(5):
            self.press('Enter', mode)
            heard[self.model.display(self.screen.current(self.app))] = self.said[-1]
            self.press('Down', mode)
        self.assertTrue(heard['slow.iso'].endswith('Space pauses or resumes it.'), heard)
        self.assertTrue(heard['lost.zip'].endswith('Space tries again.'), heard)
        self.assertTrue(heard['gone.pdf'].endswith('To get it, go back to the web page and choose the link again.'), heard)
        self.assertTrue(heard['stopped.mp3'].endswith('To get it, go back to the web page and choose the link again.'), heard)
        self.assertTrue(heard['risky.exe'].endswith('In Chrome, press Control+J, then keep or delete the file.'), heard)

    def test_a_failure_heard_away_from_the_web_names_a_way_back_that_works_there(self):
        self.model.report('edge', 'a', **edge_report(test_downloads.edge('a', new=True)))
        self.model.report('edge', 'a', **edge_report(test_downloads.edge('a', 'Interrupted', 'NetworkFailed', received=5)))
        self.screen.poll(self.app, 'document')
        self.assertTrue(self.said[-1].endswith('To try again, open Downloads and press Space.'), self.said[-1])
        # "Downloads" opens the list from the main menu, where the user hears this.
        self.assertEqual(self.app.handle('downloads', 'awake'), 'downloads')


    def test_a_downloaded_word_file_opens_in_the_document_reader_and_escape_returns(self):
        import time, zipfile
        path = self.root / 'letter.docx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                             '<Default Extension="xml" ContentType="application/xml"/>'
                             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr('word/document.xml', '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             '<w:body><w:p><w:r><w:t>Dear Will, your claim is approved.</w:t></w:r></w:p></w:body></w:document>')
        self.model.report('edge', 'letter', **edge_report(test_downloads.edge('letter', 'InProgress', new=True, path=str(path))))
        self.model.report('edge', 'letter', **edge_report(test_downloads.edge('letter', 'Completed', received=1000, path=str(path))))
        mode = self.app.handle('download manager', 'web')
        mode = self.press('Enter', mode)
        if mode == 'files':
            until = time.monotonic() + 5
            while self.app.FILE_MANAGER.events.empty() and time.monotonic() < until: time.sleep(.01)
            mode = self.app.poll_file_manager(mode)
        # The guide promises the Document reader: a download is read, not edited.
        self.assertEqual(mode, 'file_reader')
        self.assertIn('your claim is approved', ' '.join(self.said))
        self.assertEqual(self.press('Escape', mode), 'downloads')


    def test_a_damaged_download_says_so_and_stays_on_the_list(self):
        import time
        path = self.root / 'bad.pdf'; path.write_bytes(b'not a pdf at all')
        self.model.report('edge', 'bad', **edge_report(test_downloads.edge('bad', 'InProgress', new=True, path=str(path))))
        self.model.report('edge', 'bad', **edge_report(test_downloads.edge('bad', 'Completed', received=1000, path=str(path))))
        mode = self.press('Enter', self.app.handle('download manager', 'web'))
        until = time.monotonic() + 5
        while self.app.FILE_MANAGER.events.empty() and time.monotonic() < until: time.sleep(.01)
        mode = self.app.poll_file_manager(mode)
        mode = self.screen.poll(self.app, mode)
        self.assertEqual(mode, 'downloads')
        self.assertIn('This file is damaged or empty, so it could not be opened.', self.said)
        self.assertEqual(self.said[-1], 'Downloads. bad.pdf, finished, 1 of 1.')
        self.assertNotIn('Windows could not complete that file action', ' '.join(self.said))


class FileAddressTests(unittest.TestCase):
    def test_an_address_that_downloads_keeps_the_same_browser_and_its_download(self):
        from web_assistant import WebSession, BrowserBackend
        folder = tempfile.mkdtemp()
        backend = object.__new__(BrowserBackend)
        backend.browser = 'webview2'; backend.start = Mock(); backend.page = Mock(); backend.close = Mock()
        backend.page.goto.side_effect = Exception('page.goto: Download is starting')
        with patch('connected_browser.create_backend', return_value=backend):
            session = WebSession(folder)
        self.assertFalse(session.injected_backend)
        with patch('web_assistant.guided_backend') as other:
            said = session.open('https://files.test/report.zip')
        other.assert_not_called()
        backend.close.assert_not_called()
        self.assertIs(session.backend, backend)
        self.assertEqual(said, 'This address is a file, so it is downloading. Press Control+D to see your downloads.')


class Player:
    """Behaves like MediaPlayer: open() raises OSError when the stream will not start."""
    def __init__(self, fail=False):
        self.kind = None; self.fail = fail; self.opened = []
    def open(self, url, title, kind):
        self.opened.append((url, title, kind))
        if self.fail:
            self.kind = None
            raise OSError('This audio format could not start in the player.')
        self.kind = kind
    def control(self, action, seconds=15, position=None):
        return 0 if self.kind else None
    def close(self): self.kind = None


class MediaTests(unittest.TestCase):
    def setUp(self):
        from media_hub import MediaHub
        self.folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, self.folder, True)
        self.notices = []
        self.hub = MediaHub(self.folder, fetch_json=Mock(), notify=self.notices.append, player=Player())

    def companion(self, section):
        import companion as app
        stack = ExitStack(); self.addCleanup(stack.close)
        said = []
        stack.enter_context(patch.object(app, 'MEDIA_HUB', self.hub))
        stack.enter_context(patch.object(app, 'MEDIA_NAV', None, create=True))
        stack.enter_context(patch.object(app, 'MEDIA_SECTION', section))
        stack.enter_context(patch.object(app, 'TEXT_MODE', True))
        stack.enter_context(patch.object(app, 'APP_WINDOW', None))
        for name in ('speak', 'speak_prompt', 'speak_status'):
            stack.enter_context(patch.object(app, name, side_effect=lambda text, *a, **k: said.append(str(text))))
        stack.enter_context(patch.object(app, 'interrupt_speech'))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return app, said

    def test_damaged_schedule_entries_list_and_cancel_without_stopping_the_app(self):
        good = {'station': {'name': 'WGN', 'url': 'https://wgn.example/live'}, 'when': '2026-10-28T08:00:00', 'minutes': 30, 'id': '1'}
        (self.folder / 'radio-schedule.json').write_text(json.dumps(['broken', {'station': 'WXYZ'}, good]), encoding='utf-8')
        from media_navigation import MediaNavigation
        nav = MediaNavigation(self.hub, 'radio')
        said = nav.command('list scheduled recordings')
        self.assertIn('WGN', said)
        names, index, _ = nav.state()
        self.assertEqual(names, ['A damaged schedule entry', 'A damaged schedule entry', 'WGN, Wednesday, October 28 at eight AM'])
        nav.keyboard('Down')
        self.assertIn('Remove A damaged schedule entry?', nav.keyboard('Delete'))
        self.assertEqual(nav.command('yes'), 'Removed.')
        kept = json.loads((self.folder / 'radio-schedule.json').read_text(encoding='utf-8'))
        self.assertEqual(kept, ['broken', good])
        # Only the chosen entry goes; WGN stays.
        nav.command('list scheduled recordings'); nav.keyboard('End'); nav.keyboard('Delete'); nav.command('yes')
        self.assertEqual(json.loads((self.folder / 'radio-schedule.json').read_text(encoding='utf-8')), ['broken'])
        # A schedule file that is not a list is shown as empty, not as an error.
        (self.folder / 'radio-schedule.json').write_text('{"station": 1}', encoding='utf-8')
        nav.command('list scheduled recordings')
        self.assertIsNone(nav.state())

    def test_podcast_search_offline_or_with_a_damaged_answer_says_so_plainly(self):
        from urllib.error import URLError
        app, said = self.companion('podcast')
        self.hub.fetch_json.side_effect = URLError('[Errno -2] Name or service not known')
        self.assertEqual(app.handle('search podcasts for news', 'media'), 'media')
        self.assertEqual(said[-1], 'Podcast search is not available right now. Check the internet connection, then try again.')
        self.hub.fetch_json.side_effect = None
        for answer in (['not', 'a', 'dict'], {'results': ['text', None, {'collectionName': 'Real show', 'feedUrl': 'https://pod.example/feed'}]}):
            self.hub.fetch_json.return_value = answer
            app.handle('search podcasts for news', 'media')
        self.assertTrue(said[-1].startswith('Found 1 podcast. Podcast 1: Real show.'), said[-1])

    def test_play_on_a_station_whose_stream_fails_says_so_plainly(self):
        from media_navigation import MediaNavigation
        self.hub.player = Player(fail=True)
        self.hub.current_station = {'name': 'WGN', 'url': 'https://wgn.example/live'}
        nav = MediaNavigation(self.hub, 'radio'); nav.set_keyboard_mode(True)
        nav.focus = 'actions'; nav.index = [a.label for a in nav.actions].index('Play')
        self.assertEqual(nav.activate(), 'WGN could not start in the audio player. Choose another station.')

    def test_space_with_nothing_playing_says_so(self):
        from media_navigation import MediaNavigation
        for section in ('radio', 'podcast'):
            nav = MediaNavigation(self.hub, section)
            self.assertEqual(nav.keyboard('Space'), 'No audio is playing. Choose a station or episode first.')

    def test_escape_from_podcast_episodes_steps_back_to_the_media_menu_as_the_guide_says(self):
        app, said = self.companion('podcast')
        self.hub.shows = [{'name': 'History', 'feed': 'https://pod.example/feed'}]; self.hub.show_index = 0
        with patch.object(self.hub, '_feed_episodes', return_value=[{'title': 'One', 'url': 'https://pod.example/1.mp3', 'show': 'History', 'feed': 'https://pod.example/feed'}]):
            app.handle('open podcast 1', 'media')
        self.assertTrue(self.hub.browsing_episodes)
        self.assertIsNone(app.navigation_key('Escape', 'media'))   # episodes to shows
        self.assertIsNone(app.navigation_key('Escape', 'media'))   # shows to actions
        self.assertEqual(app.navigation_key('Escape', 'media'), 'media menu')
        guide = (Path(__file__).parent / 'START HERE - Veteran.txt').read_text(encoding='utf-8')
        self.assertIn('Escape goes from episodes to shows, then to actions, then to the Media menu.', guide)

    def test_damaged_subscriptions_are_skipped_not_fatal(self):
        subs = ['text', {'feed': 'https://a.example/feed'}, {'name': 'Good show', 'feed': 'https://b.example/feed', 'episodes': []}]
        (self.folder / 'podcast-subscriptions.json').write_text(json.dumps(subs), encoding='utf-8')
        app, said = self.companion('podcast')
        app.handle('list subscriptions', 'media')
        self.assertTrue(said[-1].startswith('Found 1 subscription. Subscription 1: Good show'), said[-1])
        with patch('media_hub._http', return_value=b'<rss><channel><item><title>   </title><enclosure url="https://b.example/1.mp3"/></item></channel></rss>'):
            self.assertIn('Checked 1 of 1', self.hub.refresh_subscriptions())
            said_open = self.hub.open_podcast(1)
        # A blank episode title is read as Untitled episode, not as nothing.
        self.assertIn('Episode 1: Untitled episode.', said_open)
        # The damaged entries stay in the file for a helper.
        self.assertEqual(len(json.loads((self.folder / 'podcast-subscriptions.json').read_text(encoding='utf-8'))), 3)


class PlayerWindowTests(unittest.TestCase):
    def test_a_player_window_the_user_closed_is_replaced_cleanly(self):
        """Closing the player window left its browser and driver running; the next
        file then started a second browser on the same profile, which failed once."""
        from media_player import MediaPlayer
        folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, folder, True)
        player = MediaPlayer(folder)
        old_context, old_driver = Mock(), Mock()
        player.context, player.playwright = old_context, old_driver
        player.page = Mock(); player.page.is_closed.return_value = True
        events = []
        old_context.close.side_effect = lambda: events.append('old browser closed')
        old_driver.stop.side_effect = lambda: events.append('old driver released')
        driver = Mock()
        driver.firefox.launch_persistent_context.side_effect = lambda *a, **k: events.append('new browser') or Mock(pages=[Mock()])
        with patch('shared_playwright.start', return_value=driver):
            player.start()
        self.assertEqual(events, ['old browser closed', 'old driver released', 'new browser'])
        self.assertIs(player.playwright, driver)

    def test_a_new_stream_after_ducking_ended_plays_at_full_volume(self):
        from media_player import MediaPlayer
        folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, folder, True)
        player = MediaPlayer(folder); player.start = lambda: None
        page = Mock(); page.is_closed.return_value = False; page.url = (folder / 'audio-player.html').as_uri()
        window = {}
        def evaluate(script, arg=None):
            if isinstance(arg, dict) and 'ducked' in arg and 'url' not in arg:
                window['ducked'] = arg['factor'] if arg['ducked'] else 0
            if isinstance(arg, dict) and 'url' in arg:
                window['ducked'] = arg.get('ducked', window.get('ducked', 0))
                window['volume'] = 1 * (window['ducked'] or 1)
                return True
        page.evaluate.side_effect = evaluate
        player.page = page
        player.open('https://radio.example/one', 'One', 'radio')
        player.set_ducked(True, .2)          # Companion speaks
        player.kind = None                   # the stream stopped while lowered
        player.set_ducked(False)             # speech ended
        player.open('https://radio.example/two', 'Two', 'radio')
        self.assertEqual(window['volume'], 1)

    def test_audio_controls_after_the_player_window_closed_say_nothing_is_playing(self):
        from media_player import MediaPlayer
        from media_hub import MediaHub
        folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, folder, True)
        player = MediaPlayer(folder)
        player.page = Mock(); player.page.is_closed.return_value = False; player.page.evaluate.return_value = 5
        player.kind = 'radio'
        hub = MediaHub(folder, player=player, fetch_json=Mock())
        self.assertEqual(hub.command('pause audio'), 'Audio paused.')
        player.page.is_closed.return_value = True   # the user closed the player window
        self.assertEqual(hub.command('pause audio'), 'No audio is playing. Choose a station or episode first.')
        self.assertIsNone(player.kind)


class ChromeDangerTests(unittest.TestCase):
    def test_a_file_chrome_flags_at_once_says_it_needs_an_answer(self):
        folder = Path(tempfile.mkdtemp()); self.addCleanup(__import__('shutil').rmtree, folder, True)
        model = downloads.DownloadManager(folder, background=False)
        model.attach('chrome', Mock())
        model.report('chrome', 7, **downloads.chrome_report({'id': 7, 'new': True, 'state': 'in_progress', 'danger': 'file',
                                                            'filename': 'C:\\Users\\Will\\Downloads\\tool.exe'}))
        said = []
        while not model.notices.empty(): said.append(model.notices.get_nowait())
        self.assertEqual(said, ['Downloading tool.exe.', 'tool.exe needs your answer in Chrome. Chrome thinks it may be unsafe. Press Control+J in Chrome to keep or delete it.'])
        self.assertEqual(model.labels(), ['tool.exe, waiting for your answer in Chrome'])

    def test_only_a_danger_that_needs_an_answer_waits_for_chrome(self):
        from downloads import chrome_report
        running = {'id': 1, 'state': 'in_progress', 'filename': 'C:\\Users\\Will\\Downloads\\a.pdf'}
        # Chrome is still checking the file, or a policy or a deep scan found it safe.
        for danger in ('safe', 'accepted', 'asyncScanning', 'asyncLocalPasswordScanning', 'allowlistedByPolicy', 'deepScannedSafe'):
            self.assertEqual(chrome_report({**running, 'danger': danger})['state'], 'downloading', danger)
        for danger in ('file', 'url', 'content', 'uncommon', 'host', 'unwanted', 'promptForScanning'):
            self.assertEqual(chrome_report({**running, 'danger': danger})['state'], 'blocked', danger)


class PlayerSpeedTests(unittest.TestCase):
    def test_playback_speed_takes_spoken_forms_and_explains_a_wrong_one(self):
        import companion as app
        said = []
        hub = Mock(); hub.player.kind = 'local'
        def adjust(setting, value):
            if not .5 <= value <= 3: raise ValueError('Choose a playback speed from 0.5 to 3.')
            return value
        hub.player.adjust.side_effect = adjust
        with patch.object(app, 'MEDIA_HUB', hub), patch.object(app, 'speak', side_effect=lambda t, *a, **k: said.append(t)), \
             patch.object(app, 'TEXT_MODE', True), patch.object(app, 'APP_WINDOW', None), redirect_stdout(io.StringIO()):
            nav = app.task_navigation()
            for spoken, heard in (('1.5x', 'Playback speed 1.5.'), ('Two times.', 'Playback speed 2.0.'), ('one point two five', 'Playback speed 1.25.'),
                                  ('fast', 'Say a number from 0.5 to 3, such as 1.5.'), ('9', 'Choose a playback speed from 0.5 to 3.')):
                nav.prompt = ('speed {value}', 'local_media')
                app.handle(spoken, 'task_input')
                self.assertEqual(said[-1], heard, spoken)


class FavoriteNameTests(unittest.TestCase):
    def test_a_spoken_favorite_name_keeps_no_closing_period(self):
        from web_assistant import WebSession
        folder = tempfile.mkdtemp(); self.addCleanup(__import__('shutil').rmtree, folder, True)
        session = WebSession(folder, Mock())
        session.snapshot = {'url': 'https://www.va.gov/', 'title': 'VA', 'text': 'x', 'controls': [], 'elements': []}
        session._sync_browser_tab = lambda: None
        self.assertEqual(session.command('Save favorite as VA news.'), 'Saved favorite VA news.')
        self.assertEqual([item['name'] for item in session.favorites.all().values()], ['VA news'])
        # The same through New favorite and Rename favorite in the web browser menu.
        menu = session.menu
        menu.process('new favorite', session, False)
        self.assertEqual(menu.process('Weather.', session, False)[0], 'Saved favorite Weather. Web browser menu.')
        menu.process('rename favorite', session, False); menu.process('weather', session, False)
        self.assertEqual(menu.process('Local weather!', session, False)[0], 'Renamed favorite to Local weather. Web browser menu.')
        self.assertEqual(sorted(item['name'] for item in session.favorites.all().values()), ['Local weather', 'VA news'])


class InsecureFormTests(unittest.TestCase):
    def test_a_form_on_a_page_without_https_says_why_it_is_not_sent_by_voice(self):
        from web_assistant import WebSession
        folder = tempfile.mkdtemp(); self.addCleanup(__import__('shutil').rmtree, folder, True)
        page = {'url': 'http://shop.example/order', 'title': 'Order', 'text': 'Name', 'controls': [], 'elements': []}
        backend = Mock(); backend.snapshot.return_value = page
        session = WebSession(folder, backend); session.snapshot = dict(page)
        self.assertEqual(session.prepare_submit(), 'This page is not secure, because its address does not start with https. '
                         'Forms here are not sent by voice. Ask your helper to check it in the browser.')


if __name__ == '__main__':
    unittest.main()

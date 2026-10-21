"""Regression checks for media, dictation, and updater bugs found in the October 2026 review."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_updates
import media_hub
from ai_dictation import clean_disfluencies
from media_hub import MediaHub
from media_navigation import MediaNavigation


class Player:
    def __init__(self): self.opened = []
    def open(self, url, title, kind): self.opened.append(title)


class MediaFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_station_database_form_opens_after_choosing_a_named_source(self):
        hub = MediaHub(self.folder, fetch_json=lambda url: [])
        nav = MediaNavigation(hub, 'radio')
        (self.folder / 'radiosure-stations.json').write_text(json.dumps([{'name': 'A', 'url': 'https://r.example/a'}]))
        for spoken, label in (('radio browser', 'radio browser'), ('radio sure', 'radio sure'), ('iprd', 'iprd')):
            hub.choose_source(spoken)
            nav.open_form('source')
            choices, index, _ = nav.state()
            self.assertEqual(choices[index], label)

    def test_keyboard_recordings_list_plays_the_announced_recording(self):
        player = Player()
        hub = MediaHub(self.folder, player=player)
        recordings = self.folder / 'Radio Recordings'
        recordings.mkdir()
        (recordings / 'abc Lounge 2026-10-01 08-00-00.mp3').write_bytes(b'x')
        (recordings / 'BBC Radio 2026-10-01 09-00-00.mp3').write_bytes(b'x')
        nav = MediaNavigation(hub, 'radio')
        nav.run('list recordings')
        announced = nav.items()[nav.item_index]['title']
        nav.activate()
        self.assertEqual(player.opened, [announced])
        listing = hub.command('list recordings')
        self.assertLess(listing.index('abc Lounge'), listing.index('BBC Radio'))

    def test_out_of_range_episode_number_is_refused(self):
        hub = MediaHub(self.folder)
        hub.episodes = [{'title': 'First', 'url': 'https://a.example/1.mp3', 'show': 'S', 'feed': ''},
                        {'title': 'Last', 'url': 'https://a.example/9.mp3', 'show': 'S', 'feed': ''}]
        with mock.patch('threading.Thread') as thread:
            for number in ('0', '3'):
                self.assertEqual(hub.download_episode(number), 'That episode number is unavailable.')
            thread.assert_not_called()

    def test_long_episode_names_that_differ_after_the_cut_both_download(self):
        hub = MediaHub(self.folder)
        show = 'The History of Ancient Civilizations and the People Who Built Them Podcast'
        hub.episodes = [{'title': 'Episode 101: The Rise and Fall of the Roman Republic Part ' + part,
                         'url': 'https://a.example/' + part + '.mp3', 'show': show, 'feed': ''}
                        for part in ('One', 'Two')]
        targets = []
        with mock.patch('threading.Thread') as thread:
            for number in (1, 2):
                self.assertTrue(hub.download_episode(number).startswith('Downloading'))
                target = thread.call_args.kwargs['args'][1]
                target.write_bytes(b'audio'); hub._downloads.discard(target); targets.append(target)
        self.assertNotEqual(targets[0], targets[1])
        self.assertTrue(all(len(t.stem) <= 90 for t in targets))

    def test_short_episode_names_keep_their_existing_file_name(self):
        hub = MediaHub(self.folder)
        hub.episodes = [{'title': 'Episode 1', 'url': 'https://a.example/1.mp3', 'show': 'Show', 'feed': ''}]
        with mock.patch('threading.Thread') as thread:
            hub.download_episode(1)
        self.assertEqual(thread.call_args.kwargs['args'][1].name, 'Show - Episode 1.mp3')

    def test_unfinished_downloads_are_not_listed(self):
        hub = MediaHub(self.folder)
        downloads = self.folder / 'Podcast Downloads'
        downloads.mkdir()
        (downloads / 'Show - Episode 1.mp3').write_bytes(b'x')
        (downloads / 'Show - Episode 2.mp3.part').write_bytes(b'x')
        self.assertEqual([p.name for p in hub.downloads()], ['Show - Episode 1.mp3'])

    def test_country_code_matches_whole_words_only(self):
        hub = MediaHub(self.folder, fetch_json=lambda url: (_ for _ in ()).throw(OSError('offline')))
        rows = [{'name': 'Radio Moscow', 'url': 'https://r.example/m', 'country': 'Russia'},
                {'name': 'Triple J', 'url': 'https://r.example/j', 'country': 'Australia'},
                {'name': 'WGN', 'url': 'https://r.example/w', 'country': 'United States'},
                {'name': 'KUS', 'url': 'https://r.example/k', 'country': 'US'}]
        (self.folder / 'radio-iprd-stations.json').write_text(json.dumps(rows))
        hub.choose_source('iprd')
        hub.search_radio('country', 'united states')
        self.assertEqual(sorted(s['name'] for s in hub.stations), ['KUS', 'WGN'])

    def test_feed_with_default_namespace_lists_episodes(self):
        hub = MediaHub(self.folder)
        feed = (b'<?xml version="1.0"?><rss xmlns="http://backend.userland.com/rss2" version="2.0"><channel>'
                b'<item><title>E1</title><enclosure url="https://a.example/e1.mp3"/>'
                b'<chapters xmlns="https://podcastindex.org/namespace/1.0" url="https://a.example/c.json"/></item>'
                b'</channel></rss>')
        with mock.patch.object(media_hub, '_http', lambda url, limit: feed):
            episodes = hub._feed_episodes({'feed': 'https://f.example/rss', 'name': 'S'})
        self.assertEqual([(e['title'], e['chapters_url']) for e in episodes], [('E1', 'https://a.example/c.json')])


class DictationPrefixTests(unittest.TestCase):
    def test_real_hyphenated_prefixes_are_kept(self):
        for text in ('Please re-read the contract.', 'We need to re-record the intro.',
                     'I will re-release it.', 'Co-convene the meeting.', 'Pre-pay the bill.'):
            self.assertEqual(clean_disfluencies(text), text)

    def test_stammers_are_still_collapsed(self):
        self.assertEqual(clean_disfluencies('I w-w-want this.'), 'I want this.')
        self.assertEqual(clean_disfluencies('I w-want this.'), 'I want this.')
        self.assertEqual(clean_disfluencies('th-th-this one'), 'this one')
        self.assertEqual(clean_disfluencies('Please re-re-read it.'), 'Please read it.')


class UpdateCleanupTests(unittest.TestCase):
    def test_old_update_folders_are_removed_on_start_and_after_failed_downloads(self):
        body = b'MZ' + b'\0' * 100
        release = {'version': '9.9.9', 'installer_url': 'https://x.example/s.exe',
                   'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body)}

        class Response(io.BytesIO):
            def geturl(self): return 'https://x.example/s.exe'
            def __enter__(self): return self

        with tempfile.TemporaryDirectory() as folder:
            updates = Path(folder) / 'Updates'
            (updates / 'old-download').mkdir(parents=True)
            (updates / 'old-download' / 'VoiceCompanion-Setup.exe').write_bytes(body)
            (Path(folder) / 'update-result.json').write_text('{"succeeded": true}')
            updater = app_updates.AppUpdates(folder, '0.2.151-test', folder)
            self.assertEqual(list(updates.iterdir()), [])
            self.assertTrue((Path(folder) / 'update-result.json').exists())
            updater.release = dict(release, sha256='0' * 64)
            with mock.patch('urllib.request.urlopen', lambda *a, **k: Response(body)):
                updater.download()
                while updater.busy: pass
            events = [updater.events.get_nowait()[0] for _ in range(updater.events.qsize())]
            self.assertEqual(events[-1], 'download_failed')
            self.assertEqual(list(updates.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

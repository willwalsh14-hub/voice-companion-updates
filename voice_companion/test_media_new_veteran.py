"""A new veteran in Radio and Podcasts (nightly sweep, October 29, 2026).

Someone who has never used the app follows the spoken prompts and says natural things.
These tests drive the real app through companion.handle with a real MediaHub."""
import json
import unittest
from unittest.mock import patch

import companion
from test_core_email_fixes import CompanionTestCase
from test_media_web_answers import radio_hub


EPISODES = [{'title': 'Rome', 'url': 'https://pod.test/1.mp3', 'show': 'History Hour', 'feed': 'https://pod.test/h',
             'chapters_url': '', 'published': 0},
            {'title': 'Greece', 'url': 'https://pod.test/2.mp3', 'show': 'History Hour', 'feed': 'https://pod.test/h',
             'chapters_url': '', 'published': 0}]
SHOWS = {'results': [{'collectionName': 'History Hour', 'feedUrl': 'https://pod.test/h'},
                     {'collectionName': 'Science Now', 'feedUrl': 'https://pod.test/s'}]}


class NewVeteranMediaTestCase(CompanionTestCase):
    def setUp(self):
        super().setUp()
        self.hub = radio_hub(self.folder)
        directory = self.hub.fetch_json
        self.hub.fetch_json = lambda url: SHOWS if 'itunes' in url else directory(url)
        self.hub._feed_episodes = lambda show: [dict(e) for e in EPISODES]
        for name, value in (('MEDIA_HUB', self.hub), ('MEDIA_NAV', None), ('MEDIA_SECTION', 'radio')):
            p = patch.object(companion, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(companion, 'interrupt_speech', create=True); p.start(); self.addCleanup(p.stop)
        self.mode = 'awake'

    def say(self, *phrases):
        for phrase in phrases:
            self.said.clear()
            self.mode = companion.handle(phrase, self.mode) or self.mode
        return ' '.join(self.said)


class PlayTheChosenItemTests(NewVeteranMediaTestCase):
    """The prompts say "that one to play". A new veteran says "Play" instead. With nothing
    playing yet, "Play" plays the chosen station or episode, instead of saying that no
    audio is playing."""

    def test_play_plays_the_chosen_station(self):
        self.say('Media', 'Radio', 'Search radio', 'WGN', 'Next')
        self.assertIsNone(self.hub.player.kind)
        said = self.say('Play')
        self.assertEqual(said, 'Playing WGN. Say pause or stop.')
        self.assertEqual(self.hub.player.title, 'WGN')

    def test_play_plays_the_chosen_episode(self):
        self.say('Media', 'Podcasts', 'Search podcasts for history', 'That one', 'Next')
        said = self.say('Play.')
        self.assertEqual(said, 'Playing Greece. Say pause, back 15 seconds, or next episode.')

    def test_play_on_a_show_says_how_to_reach_its_episodes(self):
        self.say('Media', 'Podcasts', 'Search podcasts for history')
        said = self.say('Play')
        self.assertEqual(said, 'History Hour is chosen. Say that one to hear its episodes, then choose one to play.')
        self.assertIsNone(self.hub.player.kind)

    def test_play_on_a_chosen_preset_plays_it(self):
        self.say('Media', 'Radio', 'Play WGN', 'Save preset as News', 'Stop')
        self.hub.player.close()
        said = self.say('List presets', 'Play')
        self.assertEqual(said, 'Playing preset News. Say pause or stop.')

    def test_play_still_resumes_what_was_paused(self):
        self.say('Media', 'Radio', 'Search radio', 'WGN', 'That one', 'Pause', 'Next')
        self.assertEqual(self.say('Play'), 'Playing audio.')
        self.assertEqual(self.hub.player.title, 'WGN Radio 720')

    def test_play_with_no_results_still_says_what_to_do(self):
        self.say('Media', 'Radio')
        self.assertEqual(self.say('Play'), 'No audio is playing. Choose a station or episode first.')


class PresetNameTests(NewVeteranMediaTestCase):
    """A preset keeps the name as said or typed. It was saved in small letters, so the
    screen showed "jazz" for "Save preset as Jazz"."""

    def test_spoken_name_keeps_its_capitals(self):
        self.say('Media', 'Radio', 'Play WGN')
        self.assertEqual(self.say('Save preset as Morning News.'), 'Saved preset Morning News. Say play preset Morning News later.')
        self.assertEqual([p['name'] for p in self.hub.presets()], ['Morning News'])
        self.assertEqual(self.say('Play preset morning news'), 'Playing preset Morning News. Say pause or stop.')

    def test_name_typed_in_the_form_keeps_its_capitals(self):
        self.say('Media', 'Radio', 'Play WGN')
        nav = companion.media_navigation()
        nav.keyboard('Up')   # the first station action, Add to favorites
        self.assertTrue(nav.keyboard('Enter').startswith('Preset name. WGN.'))
        self.assertEqual(nav.field_value('Chicago WGN'), 'Saved preset Chicago WGN. Say play preset Chicago WGN later.')


class DamagedMediaFilesTests(NewVeteranMediaTestCase):
    """A radio or podcast file of the wrong shape (a list where settings belong) stopped
    every station search, "Play WGN" and "Station database" with an error."""

    def damage(self, **files):
        for name, value in files.items():
            (self.folder / name).write_text(json.dumps(value), encoding='utf-8')

    def test_station_search_and_play_by_name_still_work(self):
        self.damage(**{'radio-failures.json': ['x'], 'radio-preferences.json': ['x'],
                       'radiosure-stations.json': ['bad', None, {'name': 'WGN Local', 'url': 'https://radio.test/local'}],
                       'radio-iprd-stations.json': {'stations': 1}})
        self.assertTrue(self.say('Media', 'Radio', 'Search radio for jazz').startswith('Found 1 station. Station 1: Jazz 24.'))
        self.assertTrue(self.say('Station database').startswith('Station database. all.'))
        self.say('Cancel')
        self.assertEqual(self.say('Main menu', 'Play WGN'), 'Playing WGN. Say pause or stop.')

    def test_a_failure_time_that_is_not_a_number_is_ignored(self):
        self.damage(**{'radio-failures.json': {'https://radio.test/jazz': 'yesterday'}})
        self.assertTrue(self.say('Media', 'Radio', 'Search radio for jazz').startswith('Found 1 station.'))

    def test_a_damaged_schedule_is_kept_and_nothing_is_scheduled(self):
        self.damage(**{'radio-schedule.json': {'broken': True}})
        self.say('Media', 'Radio', 'Search radio for jazz')
        said = self.say('Schedule station 1 tomorrow at 8 PM for 30 minutes')
        self.assertEqual(said, 'The radio schedule is damaged, so nothing was scheduled. Ask your helper to check it.')
        self.assertEqual(json.loads((self.folder / 'radio-schedule.json').read_text()), {'broken': True})

    def test_a_damaged_subscription_file_is_kept(self):
        self.damage(**{'podcast-subscriptions.json': {'broken': True}})
        said = self.say('Media', 'Podcasts', 'Search podcasts for history', 'Subscribe')
        self.assertEqual(said, 'Your podcast subscriptions file is damaged, so History Hour was not added. Ask your helper to check it.')
        self.assertEqual(json.loads((self.folder / 'podcast-subscriptions.json').read_text()), {'broken': True})

    def test_damaged_status_and_download_records_do_not_stop_the_checks(self):
        self.damage(**{'radio-iprd-status.json': ['x'], 'podcast-auto-files.json': [1]})
        self.hub.fetch_json = lambda url: {'stations': [{'name': 'IPRD One', 'streams': [{'url': 'https://radio.test/i'}]}]}
        self.assertEqual(self.hub.refresh_iprd(), 'IPRD station catalog refreshed with 1 stations.')
        self.hub._prune_auto('https://pod.test/h', set())
        self.assertEqual(json.loads((self.folder / 'podcast-auto-files.json').read_text()), {})



class AudioWordsNameWhatIsLoadedTests(unittest.TestCase):
    """"Pause radio" while a file from the Media player plays said "A podcast is selected"."""

    def test_radio_and_podcast_words_with_a_media_file(self):
        import tempfile
        from test_media_web_answers import StreamPlayer
        from media_hub import MediaHub
        with tempfile.TemporaryDirectory() as folder:
            hub = MediaHub(folder, player=StreamPlayer())
            hub.player.open('file:///C:/Music/song.mp3', 'song.mp3', 'local')
            self.assertEqual(hub.command('pause radio'), 'A media file is selected. Say pause audio.')
            self.assertEqual(hub.command('stop podcast'), 'A media file is selected. Say stop audio.')
            self.assertFalse(hub.player.element['paused'])
            hub.player.open('https://pod.test/1.mp3', 'Rome', 'podcast')
            self.assertEqual(hub.command('pause radio'), 'A podcast is selected. Say pause podcast.')
            hub.player.open('https://radio.test/wgn', 'WGN', 'radio')
            self.assertEqual(hub.command('pause podcast'), 'A station is selected. Say pause radio.')



class DeleteKeyTests(NewVeteranMediaTestCase):
    """Delete on an episode or a station result said "Choose an item first.", though one was chosen."""

    def test_delete_on_an_item_it_cannot_remove_says_what_it_removes(self):
        self.say('Media', 'Podcasts', 'Search podcasts for history', 'That one')
        nav = companion.media_navigation()
        self.assertEqual(nav.keyboard('Delete'), 'Delete removes a saved show, a preset, or a scheduled recording. It does nothing here.')
        self.say('Main menu', 'Media', 'Radio', 'Search radio for jazz')
        nav = companion.media_navigation()
        self.assertEqual(nav.keyboard('Delete'), 'Delete removes a saved show, a preset, or a scheduled recording. It does nothing here.')

    def test_delete_still_asks_before_removing_a_preset(self):
        self.say('Media', 'Radio', 'Play WGN', 'Save preset', 'List presets')
        self.assertEqual(companion.media_navigation().keyboard('Delete'), 'Delete preset WGN? Say yes or no.')



class PlayWordsThatNameNoStationTests(unittest.TestCase):
    """"Play the radio" searched for a station called "the", which matches hundreds of
    stations. A quoted name was searched with its quotes."""

    def test_filler_words_are_not_a_station(self):
        from media_hub import station_request
        for words in ('Play the radio.', 'Play it again.', 'Play something.', 'Play the station.', 'Play a radio station'):
            self.assertIsNone(station_request(words), words)

    def test_real_names_still_work(self):
        from media_hub import station_request
        self.assertEqual(station_request('Play "WGN".'), 'WGN')
        self.assertEqual(station_request('Play \u201cThe Breeze\u201d radio.'), 'The Breeze')
        self.assertEqual(station_request('Play the Breeze.'), 'the Breeze')
        self.assertEqual(station_request('Play WGN on the radio.'), 'WGN')


class PlayTheRadioOnTheRadioScreenTests(NewVeteranMediaTestCase):
    def test_play_the_radio_is_not_a_search_for_the(self):
        self.say('Media', 'Radio')
        searched = []
        search = self.hub.search_radio
        self.hub.search_radio = lambda kind, value: (searched.append(value), search(kind, value))[1]
        self.say('Play the radio.')
        self.assertEqual(searched, [])
        self.assertEqual(self.say('Listen to WGN.'), 'Playing WGN. Say pause or stop.')


if __name__ == '__main__':
    unittest.main()

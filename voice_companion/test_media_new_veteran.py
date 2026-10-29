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


if __name__ == '__main__':
    unittest.main()

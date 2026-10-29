"""Playing podcasts and radio in Companion's own window (tester, October 2026).

"I dislike how it pulls up another window ... And it starts the podcast very loud."
The player now runs without a window, starts at half volume, keeps the level the
user picks, and shows its controls as a list on the Podcasts or Radio screen.

The real MediaPlayer is used. Only its browser page is faked, and the fake answers
each page script the way the real page does (test_consumer_audio checks the real page).
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import media_player
from media_hub import MediaHub
from media_navigation import CONTROLS, MediaNavigation, PODCASTS, RADIO
from media_player import DEFAULT_VOLUME, MediaPlayer, VOLUME_FILE


class FakeAudioPage:
    """The player page: one <audio> element and the values Companion keeps on window."""
    def __init__(self):
        self.closed = False
        self.url = ''
        self.audio = {'src': '', 'paused': True, 'time': 0.0, 'duration': 600.0, 'volume': 1.0, 'rate': 1.0}
        self.window = {}
        self.scripts = []

    def is_closed(self): return self.closed
    def goto(self, url): self.url = url

    def evaluate(self, script, args=None):
        self.scripts.append(script)
        a, w = self.audio, self.window
        if isinstance(args, dict) and 'url' in args:   # open()
            w['volume'], w['ducked'] = args['volume'], args['ducked']
            a.update(src=args['url'], paused=False, time=0.0, rate=1.0)   # load() resets the speed
            a['volume'] = args['volume'] * (args['ducked'] or 1)
            return True
        if isinstance(args, dict) and 'setting' in args:   # adjust()
            if args['setting'] == 'volume':
                w['volume'] = args['value'];a['volume'] = args['value'] * (w.get('ducked') or 1);return args['value']
            a['rate'] = args['value'];return a['rate']
        if isinstance(args, dict) and 'ducked' in args:   # set_ducked()
            w['ducked'] = args['factor'] if args['ducked'] else 0
            a['volume'] = w.get('volume', args['volume']) * (args['factor'] if args['ducked'] else 1)
            return None
        if isinstance(args, dict) and 'action' in args:   # control()
            action = args['action']
            if action == 'toggle':
                if not a['paused']:a['paused'] = True;return True
                a['paused'] = False;return False
            if action in ('pause', 'stop'):
                a['paused'] = True
                if action == 'stop':a['time'] = 0.0
                return a['time']
            if action == 'play':a['paused'] = False;return a['time']
            if action == 'seek':
                p = a['time'] + args['seconds'] if args['position'] is None else args['position']
                a['time'] = max(0.0, min(a['duration'], p));return a['time']
            return a['time']
        if 'currentTime' in script and 'duration' in script:   # status()
            return {'paused': a['paused'], 'position': a['time'], 'duration': a['duration']}
        return None


def player_in(folder):
    player = MediaPlayer(folder)
    page = FakeAudioPage()
    player.page = page
    player.start = lambda: None
    return player, page


class PlayerWindowTests(unittest.TestCase):
    def test_the_player_starts_without_a_window(self):
        launched = {}

        class Firefox:
            def launch_persistent_context(self, folder, **options):
                launched.update(options)
                return type('Context', (), {'pages': [FakeAudioPage()], 'close': lambda self: None})()

        driver = type('Driver', (), {'firefox': Firefox(), 'stop': lambda self: None})()
        with tempfile.TemporaryDirectory() as folder, patch('shared_playwright.start', return_value=driver):
            player = MediaPlayer(Path(folder) / 'Audio Player')
            player.start()
            player.close()
        self.assertIs(launched.get('headless'), True)
        self.assertEqual(launched['firefox_user_prefs'], {'media.autoplay.default': 0})

    def test_nothing_in_the_player_brings_a_window_forward(self):
        source = Path(media_player.__file__).read_text(encoding='utf-8')
        for call in ('bring_to_front', 'headless=False', 'SetForegroundWindow', '.focus('):
            self.assertNotIn(call, source)


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_audio_starts_at_half_volume(self):
        player, page = player_in(self.folder)
        self.assertEqual(DEFAULT_VOLUME, 0.5)
        player.open('https://pod.example/1.mp3', 'One', 'podcast')
        self.assertEqual(page.audio['volume'], 0.5)

    def test_the_chosen_volume_is_kept_for_next_time(self):
        player, page = player_in(self.folder)
        player.open('https://pod.example/1.mp3', 'One', 'podcast')
        self.assertAlmostEqual(player.adjust('volume', 0.1), 0.6)
        self.assertEqual(json.loads((self.folder / VOLUME_FILE).read_text()), {'volume': 0.6})
        again, page = player_in(self.folder)
        again.open('https://radio.example/live', 'Station', 'radio')
        self.assertEqual(page.audio['volume'], 0.6)
        # Never more than full, never less than silent.
        for _ in range(8):again.adjust('volume', 0.1)
        self.assertEqual(again.volume, 1.0)

    def test_a_damaged_volume_file_means_half_volume_and_is_replaced_whole(self):
        for content in ('{', '[]', '{"volume": "loud"}', '{"volume": true}'):
            with self.subTest(content=content):
                (self.folder / VOLUME_FILE).write_text(content)
                self.assertEqual(MediaPlayer(self.folder).volume, 0.5)
        (self.folder / VOLUME_FILE).write_text('{"volume": 7}')
        self.assertEqual(MediaPlayer(self.folder).volume, 1.0)
        player, _ = player_in(self.folder)
        player.kind = 'podcast'
        player.adjust('volume', -0.3)
        self.assertEqual(json.loads((self.folder / VOLUME_FILE).read_text()), {'volume': 0.7})
        self.assertEqual([p.name for p in self.folder.iterdir()], [VOLUME_FILE])

    def test_lowering_for_speech_keeps_the_chosen_level(self):
        player, page = player_in(self.folder)
        player.open('https://pod.example/1.mp3', 'One', 'podcast')
        player.set_ducked(True, 0.25)
        self.assertAlmostEqual(page.audio['volume'], 0.125)
        player.set_ducked(False, 0.25)
        self.assertEqual(page.audio['volume'], 0.5)


class LevelCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.player, self.page = player_in(Path(self.temp.name) / 'Audio Player')
        self.hub = MediaHub(self.temp.name, player=self.player)

    def test_nothing_playing(self):
        self.assertEqual(self.hub.command('Audio volume up.', section='podcast'),
                         'No audio is playing. Choose a station or episode first.')

    def test_volume_and_speed_by_voice(self):
        self.player.open('https://pod.example/1.mp3', 'One', 'podcast')
        self.assertEqual(self.hub.command('audio volume up', section='podcast'), 'Volume 60 percent.')
        self.assertEqual(self.hub.command('quieter audio', section='podcast'), 'Volume 50 percent.')
        self.assertEqual(self.hub.command('Faster audio.', section='podcast'), 'Speed 1.25.')
        self.assertEqual(self.page.audio['rate'], 1.25)
        self.assertEqual(self.hub.command('slower audio', section='podcast'), 'Normal speed.')
        self.assertEqual(self.hub.command('slower audio', section='podcast'), 'Speed 0.75.')
        self.assertEqual(self.hub.command('slower audio', section='podcast'), 'Speed 0.5.')
        self.assertEqual(self.hub.command('slower audio', section='podcast'), 'Slowest speed. Speed 0.5.')
        self.assertEqual(self.hub.command('normal audio speed', section='podcast'), 'Normal speed.')
        self.player.volume = 1.0
        self.assertEqual(self.hub.command('audio louder', section='podcast'), 'Loudest. Volume 100 percent.')

    def test_a_live_station_keeps_normal_speed(self):
        self.player.open('https://radio.example/live', 'Station', 'radio')
        self.assertEqual(self.hub.command('faster audio', section='radio'), 'A live station always plays at normal speed.')
        self.assertEqual(self.page.audio['rate'], 1.0)


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.player, self.page = player_in(Path(self.temp.name) / 'Audio Player')
        self.hub = MediaHub(self.temp.name, player=self.player)
        self.hub.shows = [{'name': 'Show', 'feed': 'https://pod.example/feed'}];self.hub.show_index = 0
        self.hub.episodes = [{'title': 'First', 'url': 'https://pod.example/1.mp3', 'show': 'Show'},
                             {'title': 'Second', 'url': 'https://pod.example/2.mp3', 'show': 'Show'}]
        self.hub.browsing_episodes = True;self.hub.episode_index = 0
        self.nav = MediaNavigation(self.hub, 'podcast')

    def test_enter_plays_the_episode_and_opens_its_controls(self):
        said = self.nav.keyboard('Enter')
        self.assertEqual(said, 'Playing First. Player controls. Play or pause (P), 1 of 11. Escape goes back to the episodes.')
        self.assertEqual((self.nav.view, self.nav.focus), ('controls', 'items'))
        self.assertEqual([item['name'] for item in self.nav.items()],
                         ['Play or pause', 'Back 30 seconds', 'Forward 30 seconds', 'Faster', 'Slower', 'Louder', 'Quieter',
                          'Next episode', 'Previous episode', "What's playing", 'Stop'])
        self.assertEqual(self.nav.keyboard('Enter'), 'Audio paused.')
        self.assertTrue(self.page.audio['paused'])
        self.assertEqual(self.nav.keyboard('Enter'), 'Playing audio.')
        self.nav.keyboard('Down');self.nav.keyboard('Down')
        self.assertIn('Moved forward 30 seconds', self.nav.keyboard('Enter'))
        self.nav.keyboard('Up')
        self.assertIn('Moved back to the start of the episode', self.nav.keyboard('Enter'))
        self.nav.keyboard('Letter:l')
        self.assertEqual(self.nav.keyboard('Enter'), 'Volume 60 percent.')

    def test_next_episode_plays_it_and_the_controls_stay(self):
        self.nav.keyboard('Enter')
        self.nav.keyboard('End');self.nav.keyboard('Up');self.nav.keyboard('Up');self.nav.keyboard('Up')
        self.assertEqual(self.nav.announce(), 'Next episode (N), 8 of 11.')
        self.assertTrue(self.nav.keyboard('Enter').startswith('Playing Second.'))
        self.assertEqual(self.nav.view, 'controls')
        self.assertEqual(self.nav.keyboard('Escape'), 'Second (S), 2 of 2.')   # back on the one playing now
        self.nav.command('player controls');self.nav.keyboard('End');self.nav.keyboard('Up');self.nav.keyboard('Up');self.nav.keyboard('Up')
        self.assertEqual(self.page.audio['src'], 'https://pod.example/2.mp3')
        self.assertEqual(self.nav.view, 'controls')
        self.assertEqual(self.nav.keyboard('Enter'), 'There is no next episode.')
        self.nav.keyboard('Down');self.nav.keyboard('Down')
        self.assertTrue(self.nav.keyboard('Enter').startswith('Podcast: Show. Episode: Second. Playing.'))

    def test_escape_goes_back_to_the_episodes_and_the_audio_keeps_playing(self):
        self.nav.keyboard('Enter')
        self.assertEqual(self.nav.keyboard('Escape'), 'First (F), 1 of 2.')
        self.assertEqual(self.nav.view, 'episodes')
        self.assertFalse(self.page.audio['paused'])

    def test_controls_by_voice_and_from_the_actions(self):
        self.assertEqual(self.nav.command('player controls'), 'Nothing is playing. Choose an episode first.')
        self.nav.command('that one')
        self.assertEqual(self.nav.view, 'controls')
        self.assertEqual(self.nav.command('next'), 'Back 30 seconds (B), 2 of 11.')
        self.assertIn('Moved back', self.nav.command('that one'))
        self.assertIn('Player controls', [a.label for a in PODCASTS])
        self.assertIn('Player controls', [a.label for a in RADIO])

    def test_radio_controls_are_short(self):
        self.assertEqual([label for label, _ in CONTROLS['radio']], ['Play or pause', 'Louder', 'Quieter', "What's playing", 'Stop'])
        self.hub.stations = [{'name': 'WGN', 'url': 'https://radio.example/wgn'}];self.hub.station_index = 0
        nav = MediaNavigation(self.hub, 'radio')
        nav.enter('radio')
        nav.command('play station 1')
        self.assertEqual(nav.command('player controls'), 'Player controls. Play or pause (P), 1 of 5. Escape goes back to the stations.')
        nav.keyboard('Down')
        self.assertEqual(nav.keyboard('Enter'), 'Volume 60 percent.')
        self.assertEqual(nav.keyboard('Escape'), 'WGN (W), 1 of 1.')


class KeyboardDuckingTests(unittest.TestCase):
    """A screen reader's speech is not heard by Companion. While keys are pressed in
    Companion's window, radio and podcasts get quieter, and come back after a pause."""
    def setUp(self):
        import companion
        self.companion = companion
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.player, self.page = player_in(Path(self.temp.name))
        self.player.open('https://pod.example/1.mp3', 'One', 'podcast')
        self.window = type('Window', (), {'last_navigation': 0.0})()
        self.now = 1000.0
        self.prefs = dict(companion.PREFERENCES, duck_audio=True, duck_level='strong, 75 percent quieter', duck_while_keys=True)
        for name, value in (('APP_WINDOW', self.window), ('MEDIA_HUB', type('Hub', (), {'player': self.player})()),
                            ('PREFERENCES', self.prefs), ('SPEECH_PAUSED', False)):
            patcher = patch.object(companion, name, value);patcher.start();self.addCleanup(patcher.stop)
        for name, value in (('speech_busy', lambda: False),):
            patcher = patch.object(companion, name, value);patcher.start();self.addCleanup(patcher.stop)
        patcher = patch.object(companion.time, 'monotonic', lambda: self.now);patcher.start();self.addCleanup(patcher.stop)

    def ducker(self, available):
        ducker = type('Ducker', (), {'available': available, 'requests': [],
                                     'request': lambda self, *args: self.requests.append(args)})()
        patcher = patch.object(self.companion, 'AUDIO_DUCKER', ducker);patcher.start();self.addCleanup(patcher.stop)
        return ducker

    def press_key(self):
        self.window.last_navigation = self.now

    def test_on_by_default_in_radio_and_podcast_options(self):
        from settings_model import DEFAULTS, fields
        self.assertIs(DEFAULTS['duck_while_keys'], True)
        for category in ('Radio', 'Podcasts'):
            labels = {f.key: f.label for f in fields(category, {})}
            self.assertEqual(labels['duck_while_keys'], 'Lower the volume while I use the keyboard')

    def test_keys_lower_the_player_and_it_comes_back_after_a_quiet_moment(self):
        self.ducker(False)   # Windows audio not reachable: the page is lowered
        self.press_key();self.companion.update_audio_ducking()
        self.assertAlmostEqual(self.page.audio['volume'], 0.125)
        self.assertFalse(self.page.audio['paused'])   # never stopped
        self.now += 1.0;self.companion.update_audio_ducking()
        self.assertAlmostEqual(self.page.audio['volume'], 0.125)
        self.now += 0.6;self.companion.update_audio_ducking()
        self.assertEqual(self.page.audio['volume'], 0.5)

    def test_with_windows_ducking_keys_lower_like_speech_and_never_twice(self):
        ducker = self.ducker(True)
        self.press_key();self.companion.update_audio_ducking()
        self.assertEqual(ducker.requests[-1], (True, 0.25, False))
        self.assertEqual(self.page.audio['volume'], 0.5)   # Windows lowers it; the page does not again
        self.now += 2;self.companion.update_audio_ducking()
        self.assertEqual(ducker.requests[-1], (False, 0.25, False))

    def test_speech_never_waits_for_the_player_page(self):
        self.ducker(True)
        self.press_key()
        before = len(self.page.scripts)
        self.companion.update_audio_ducking(True)
        self.assertEqual(len(self.page.scripts), before)

    def test_option_off_or_nothing_loaded_or_audio_ducking_off(self):
        self.ducker(False)
        self.prefs['duck_while_keys'] = False
        self.press_key();self.companion.update_audio_ducking()
        self.assertEqual(self.page.audio['volume'], 0.5)
        self.prefs['duck_while_keys'] = True
        # Audio ducking (for Companion's voice) off: keys still lower the player, in the page.
        ducker = self.ducker(True)
        self.prefs['duck_audio'] = False
        self.companion.update_audio_ducking()
        self.assertEqual(ducker.requests[-1][0], False)
        self.assertAlmostEqual(self.page.audio['volume'], 0.125)
        self.player.kind = None
        self.assertFalse(self.companion.keys_lowering())


if __name__ == '__main__':
    unittest.main()

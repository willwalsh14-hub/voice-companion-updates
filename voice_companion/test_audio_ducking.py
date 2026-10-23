"""Audio ducking: browser and media sound gets quieter while Companion speaks.

The Windows audio layer is faked at its boundary. The fake behaves like Core
Audio: volumes are 32-bit floats from 0.0 to 1.0, a value outside that range is
refused, and a session whose program closed raises OSError
(AUDCLNT_E_DEVICE_INVALIDATED) on every call.
"""
import json
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import audio_ducking
from audio_ducking import AudioDucker, LEVEL_CHOICES, level_factor, spoken_level

OWN = 4000
INVALIDATED = -2004287484  # AUDCLNT_E_DEVICE_INVALIDATED


def float32(value):
    return struct.unpack('f', struct.pack('f', value))[0]


class FakeSession:
    def __init__(self, key, pid, name, volume=1.0):
        self.key, self.pid, self.name = key, pid, name
        self._volume = float32(volume)
        self.ended = False
        self.history = []

    def get_volume(self):
        if self.ended:
            raise OSError(INVALIDATED, 'The audio session ended.')
        return self._volume

    def set_volume(self, value):
        if self.ended:
            raise OSError(INVALIDATED, 'The audio session ended.')
        if not 0.0 <= value <= 1.0:
            raise OSError(-2147024809, 'E_INVALIDARG')
        self._volume = float32(value)
        self.history.append(self._volume)


class FakeWindows:
    """Stands in for WindowsAudioSessions."""

    def __init__(self):
        self.list = []
        # pid: (parent pid, exe name)
        self.tree = {OWN: (900, 'voicecompanion.exe'), 900: (4, 'explorer.exe'),
                     4100: (OWN, 'voicecompanion-webview2host.exe'), 4200: (4100, 'msedgewebview2.exe'),
                     4300: (OWN, 'node.exe'), 4400: (4300, 'firefox.exe'), 4500: (4400, 'firefox.exe'),
                     5000: (900, 'chrome.exe'), 5100: (5000, 'chrome.exe'),
                     6000: (900, 'spotify.exe'), 7000: (900, 'nvda.exe'), 8000: (OWN, 'vlc.exe')}
        self.times = {OWN: 100, 900: 1, 4100: 110, 4200: 120, 4300: 105, 4400: 130, 4500: 131,
                      5000: 50, 5100: 51, 6000: 20, 7000: 5, 8000: 140}

    def add(self, key, pid, volume=1.0):
        session = FakeSession(key, pid, self.tree[pid][1], volume)
        self.list.append(session)
        return session

    def sessions(self):
        return [s for s in self.list if not s.ended]

    def processes(self):
        return dict(self.tree)

    def created(self, pid):
        return self.times.get(pid)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class DuckerTests(unittest.TestCase):
    def setUp(self):
        self.windows = FakeWindows()
        self.clock = Clock()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'audio-ducking.json'
        self.logged = []
        self.ducker = self.make()

    def make(self):
        return AudioDucker(lambda: self.windows, self.state, own_pid=OWN, clock=self.clock, log=self.logged.append)

    def run_for(self, seconds, ducker=None):
        ducker = ducker or self.ducker
        end = self.clock.now + seconds
        while self.clock.now < end - 1e-9:
            self.clock.now += audio_ducking.FADE_STEP_SECONDS
            ducker.step()

    def speak(self, seconds=0.5, factor=0.25, other_apps=False, ducker=None):
        ducker = ducker or self.ducker
        end = self.clock.now + seconds
        while self.clock.now < end - 1e-9:
            ducker.request(True, factor, other_apps)
            self.clock.now += audio_ducking.FADE_STEP_SECONDS
            ducker.step()
        ducker.request(False, factor, other_apps)

    def test_browser_sound_from_other_programs_is_lowered_and_restored(self):
        user_chrome = self.windows.add('chrome', 5100, 0.8)
        embedded_edge = self.windows.add('webview', 4200)
        radio = self.windows.add('radio', 4500, 0.6)
        music = self.windows.add('music', 6000)
        reader = self.windows.add('nvda', 7000)
        speech = self.windows.add('own speech', OWN)
        self.speak()
        self.assertAlmostEqual(user_chrome.get_volume(), 0.2, places=5)
        self.assertAlmostEqual(embedded_edge.get_volume(), 0.25, places=5)
        self.assertAlmostEqual(radio.get_volume(), 0.15, places=5)
        for untouched in (music, reader, speech):
            self.assertEqual(untouched.get_volume(), 1.0)
            self.assertEqual(untouched.history, [])
        self.run_for(3)
        self.assertEqual(user_chrome.get_volume(), float32(0.8))
        self.assertEqual(embedded_edge.get_volume(), 1.0)
        self.assertEqual(radio.get_volume(), float32(0.6))
        self.assertEqual(self.ducker.records, {})

    def test_sound_started_by_companion_is_lowered_but_a_reused_process_number_is_not(self):
        player = self.windows.add('player', 8000)
        self.speak()
        self.assertAlmostEqual(player.get_volume(), 0.25, places=5)
        self.run_for(3)
        # Windows reused Companion's number for an older parent: not ours.
        self.windows.times[8000] = 10
        self.ducker.decisions.clear()
        self.speak()
        self.assertEqual(player.get_volume(), 1.0)

    def test_sound_stays_lowered_between_phrases_then_comes_back_smoothly(self):
        chrome = self.windows.add('chrome', 5100)
        self.speak(0.3)
        self.run_for(audio_ducking.HOLD_SECONDS - 0.1)
        self.assertAlmostEqual(chrome.get_volume(), 0.25, places=5)
        before = len(chrome.history)
        self.run_for(2)
        rising = chrome.history[before:]
        self.assertGreaterEqual(len(rising), 5, 'The volume must come back in small steps.')
        self.assertEqual(rising, sorted(rising))
        self.assertEqual(rising[-1], 1.0)

    def test_session_that_ends_while_lowered_does_not_stop_others_and_is_put_back_later(self):
        chrome = self.windows.add('chrome', 5100, 0.9)
        edge = self.windows.add('webview', 4200)
        self.speak()
        chrome.ended = True  # The tab closed while Companion spoke.
        self.run_for(3)
        self.assertEqual(edge.get_volume(), 1.0)
        # Windows keeps the lowered level for Chrome's next session.
        reopened = self.windows.add('chrome again', 5100, 0.225)
        self.run_for(0.1)
        self.speak(0.2)
        self.run_for(3)
        self.assertEqual(reopened.get_volume(), float32(0.9))

    def test_session_that_starts_while_speaking_is_lowered_and_restored(self):
        self.windows.add('chrome', 5100)
        self.ducker.request(True, 0.5, False)
        self.run_for(0.2)
        late = self.windows.add('video', 4200)
        self.speak(0.3, factor=0.5)
        self.assertAlmostEqual(late.get_volume(), 0.5, places=5)
        self.run_for(3)
        self.assertEqual(late.get_volume(), 1.0)

    def test_slider_moved_while_lowered_is_respected(self):
        chrome = self.windows.add('chrome', 5100)
        self.speak()
        chrome._volume = float32(0.4)  # The user moved Chrome's slider in the volume mixer.
        self.speak(0.3)
        self.run_for(3)
        self.assertEqual(chrome.get_volume(), float32(0.4))

    def test_level_change_while_speaking_moves_to_the_new_level(self):
        chrome = self.windows.add('chrome', 5100)
        self.speak(0.3, factor=0.25)
        self.speak(1.0, factor=0.75)
        self.assertAlmostEqual(chrome.get_volume(), 0.75, places=5)

    def test_other_apps_only_when_chosen_and_restored_when_turned_off(self):
        music = self.windows.add('music', 6000)
        reader = self.windows.add('nvda', 7000)
        self.speak(other_apps=True)
        self.assertAlmostEqual(music.get_volume(), 0.25, places=5)
        self.assertEqual(reader.get_volume(), 1.0)
        self.ducker.request(True, 0.25, False)
        self.run_for(0.1)
        self.assertEqual(music.get_volume(), 1.0)

    def test_volumes_left_low_by_a_crash_come_back_at_next_start(self):
        chrome = self.windows.add('chrome', 5100, 0.7)
        self.ducker.request(True, 0.25, False)
        self.run_for(0.3)
        saved = json.loads(self.state.read_text(encoding='utf-8'))
        self.assertAlmostEqual(saved['chrome.exe'], 0.7, places=5)
        # The app stops here without restoring. A new copy starts.
        restarted = self.make()
        restarted.recover()
        self.run_for(0.1, restarted)
        self.assertEqual(chrome.get_volume(), float32(0.7))
        self.assertFalse(self.state.exists())

    def test_state_file_is_removed_once_everything_is_back(self):
        self.windows.add('chrome', 5100)
        self.speak()
        self.assertTrue(self.state.exists())
        self.run_for(3)
        self.assertFalse(self.state.exists())

    def test_nothing_is_written_when_no_browser_is_playing(self):
        self.windows.add('music', 6000)
        self.speak()
        self.run_for(3)
        self.assertFalse(self.state.exists())

    def test_close_puts_sound_back_at_once_from_the_worker(self):
        chrome = self.windows.add('chrome', 5100, 0.8)
        ducker = AudioDucker(lambda: self.windows, self.state, own_pid=OWN, log=self.logged.append).start()
        ducker.request(True, 0.25, False)
        for _ in range(200):
            if abs(chrome.get_volume() - 0.2) < 1e-5:
                break
            threading.Event().wait(0.01)
        self.assertAlmostEqual(chrome.get_volume(), 0.2, places=5)
        ducker.request(True, 0.25, False)
        ducker.close()
        self.assertFalse(ducker.thread.is_alive())
        self.assertEqual(chrome.get_volume(), float32(0.8))
        self.assertFalse(self.state.exists())
        ducker.close()  # A second close (atexit after a normal close) is harmless.

    def test_speech_never_waits_for_a_stuck_windows_audio_call(self):
        chrome = self.windows.add('chrome', 5100)
        entered, release = threading.Event(), threading.Event()
        original = chrome.get_volume
        def stuck():
            entered.set();release.wait(5);return original()
        chrome.get_volume = stuck
        ducker = AudioDucker(lambda: self.windows, self.state, own_pid=OWN, log=self.logged.append).start()
        try:
            ducker.request(True, 0.25, False)
            self.assertTrue(entered.wait(2))
            done = threading.Event()
            threading.Thread(target=lambda: (ducker.request(False, 0.5, True), done.set()), daemon=True).start()
            self.assertTrue(done.wait(0.5), 'A speech request waited on Windows audio.')
            started = time.monotonic()
            ducker.close(timeout=0.3)
            self.assertLess(time.monotonic() - started, 2, 'Closing must not hang.')
        finally:
            release.set()

    def test_frequent_speech_checks_do_not_flood_windows_audio(self):
        self.windows.add('chrome', 5100)
        calls = []
        original = self.windows.sessions
        self.windows.sessions = lambda: (calls.append(1), original())[1]
        ducker = AudioDucker(lambda: self.windows, self.state, own_pid=OWN, log=self.logged.append).start()
        self.addCleanup(ducker.close)
        ducker.request(True, 0.25, False)
        time.sleep(0.3)
        before = len(calls)
        for _ in range(100):  # The app's poll loop asks again and again while speaking.
            ducker.request(True, 0.25, False);time.sleep(0.005)
        self.assertLessEqual(len(calls) - before, 6, 'Each speech check must not list audio sessions again.')

    def test_windows_audio_unavailable_is_reported_once_and_never_raises(self):
        def broken():
            raise OSError('No audio device.')
        ducker = AudioDucker(broken, self.state, own_pid=OWN, clock=self.clock, log=self.logged.append)
        self.speak(ducker=ducker)
        self.assertIs(ducker.available, False)
        self.assertEqual(len(self.logged), 1)

    def test_sessions_list_failure_mid_way_keeps_working(self):
        chrome = self.windows.add('chrome', 5100)
        self.speak(0.2)
        original = self.windows.sessions
        self.windows.sessions = Mock(side_effect=OSError(INVALIDATED, 'Device removed.'))
        self.run_for(0.2)
        self.windows.sessions = original
        self.run_for(3)
        self.assertEqual(chrome.get_volume(), 1.0)


class LevelTests(unittest.TestCase):
    def test_levels_say_what_they_mean(self):
        self.assertEqual(LEVEL_CHOICES, ('light, 25 percent quieter', 'medium, 50 percent quieter', 'strong, 75 percent quieter'))
        self.assertEqual([level_factor(x) for x in LEVEL_CHOICES], [0.75, 0.5, 0.25])
        self.assertEqual(level_factor(None), 0.25)
        self.assertEqual(level_factor('nonsense'), 0.25)

    def test_spoken_levels_tolerate_speech_punctuation(self):
        for said, index in [('Strong.', 2), ('strong', 2), ('Medium!', 1), ('light', 0), ('25 percent', 0), ('50%', 1),
                            ('Seventy five percent.', 2), ('75 percent quieter', 2), ('high', 2), ('low', 0),
                            ('Light, 25 percent quieter', 0), ('half', 1)]:
            self.assertEqual(spoken_level(said), LEVEL_CHOICES[index], said)
        for said in ('', 'loud', '40 percent', 'strong coffee please'):
            self.assertIsNone(spoken_level(said), said)


class SettingsAndCommandTests(unittest.TestCase):
    def test_voice_options_have_on_off_level_and_other_apps(self):
        from settings_model import DEFAULTS, SettingsSession, fields
        voice = {f.key: f for f in fields('Voice options', {})}
        self.assertEqual(voice['duck_audio'].kind, 'check')
        self.assertEqual(voice['duck_level'].choices, LEVEL_CHOICES)
        self.assertEqual(voice['duck_other_apps'].kind, 'check')
        self.assertTrue(DEFAULTS['duck_audio'])
        self.assertEqual(DEFAULTS['duck_level'], 'strong, 75 percent quieter')
        self.assertFalse(DEFAULTS['duck_other_apps'])
        session = SettingsSession(DEFAULTS, {})
        self.assertEqual(session.set('duck_level', 'Medium.'), 'Ducking level medium, 50 percent quieter')
        self.assertEqual(session.voice_setting('ducking level light'), ('duck_level', 'light'))
        with self.assertRaises(ValueError):
            session.set('duck_level', 'loud')

    def test_spoken_commands(self):
        import companion
        for said, expected in [('Turn audio ducking off.', ('duck_audio', False)), ('audio ducking on', ('duck_audio', True)),
                               ('Turn off audio ducking', ('duck_audio', False)), ('Enable ducking.', ('duck_audio', True)),
                               ('Ducking off', ('duck_audio', False)),
                               ('Ducking level, strong.', ('duck_level', LEVEL_CHOICES[2])),
                               ('Set the audio ducking level to 50 percent.', ('duck_level', LEVEL_CHOICES[1])),
                               ('ducking level light', ('duck_level', LEVEL_CHOICES[0])),
                               ('Ducking level loud', ('duck_level', None)),
                               ('Lower other apps on.', ('duck_other_apps', True)),
                               ('turn off lowering other apps', ('duck_other_apps', False))]:
            self.assertEqual(companion.audio_ducking_request(companion.normalized_command(said)), expected, said)
        for sentence in ('The ducks are off to the pond.', 'Ducking under the fence was hard.', 'Audio ducking is great.'):
            self.assertIsNone(companion.audio_ducking_request(companion.normalized_command(sentence)), sentence)

    def test_commands_change_and_save_the_setting(self):
        import companion
        spoken = []
        prefs = dict(companion.PREFERENCES)
        with patch.object(companion, 'PREFERENCES', prefs), patch.object(companion, 'save_preferences') as save, \
             patch.object(companion, 'speak', side_effect=spoken.append):
            self.assertEqual(companion.handle('Turn audio ducking off.', 'awake'), 'awake')
            self.assertIs(prefs['duck_audio'], False)
            self.assertEqual(companion.handle('Ducking level, medium.', 'awake'), 'awake')
            self.assertEqual(prefs['duck_level'], LEVEL_CHOICES[1])
            self.assertEqual(save.call_count, 2)
        self.assertEqual(spoken, ['Audio ducking off. Browser and media sound stays at full volume.',
                                  'Ducking level medium, 50 percent quieter.'])

    def test_unknown_level_explains_choices_but_is_text_while_writing(self):
        import companion
        spoken = []
        with patch.object(companion, 'speak', side_effect=spoken.append), patch.object(companion, 'save_preferences') as save:
            companion.handle('Ducking level, loud.', 'awake')
            self.assertIn('Choose a ducking level: light, medium, or strong.', spoken[0])
            spoken.clear()
            with patch.object(companion, 'writing_dictation', return_value=True):
                companion.handle('Ducking level, loud.', 'awake')
        save.assert_not_called()
        self.assertFalse(any('ducking level' in text.lower() for text in spoken), spoken)


class CompanionDuckingTests(unittest.TestCase):
    def test_speech_asks_the_ducker_with_the_chosen_level_and_options(self):
        import companion
        ducker = Mock(available=True)
        player = Mock()
        prefs = dict(companion.PREFERENCES, duck_audio=True, duck_level=LEVEL_CHOICES[1], duck_other_apps=True)
        with patch.object(companion, 'AUDIO_DUCKER', ducker), patch.object(companion, 'PREFERENCES', prefs), \
             patch.object(companion, 'MEDIA_HUB', Mock(player=player)), patch.object(companion, 'SPEECH_PAUSED', False), \
             patch.object(companion, 'speech_busy', return_value=True):
            companion.update_audio_ducking()
            ducker.request.assert_called_with(True, 0.5, True)
            # Windows session ducking covers the radio player: never lowered twice.
            player.set_ducked.assert_called_with(False, 0.5)
            prefs['duck_audio'] = False
            companion.update_audio_ducking(True)
            ducker.request.assert_called_with(False, 0.5, False)

    def test_radio_is_lowered_in_the_page_when_windows_ducking_is_unavailable(self):
        import companion
        player = Mock()
        prefs = dict(companion.PREFERENCES, duck_audio=True, duck_level=LEVEL_CHOICES[0])
        with patch.object(companion, 'AUDIO_DUCKER', Mock(available=False)), patch.object(companion, 'PREFERENCES', prefs), \
             patch.object(companion, 'MEDIA_HUB', Mock(player=player)):
            companion.update_audio_ducking(True)
        player.set_ducked.assert_called_with(True, 0.75)

    def test_no_ducker_outside_windows_or_in_text_mode(self):
        import companion
        with patch.object(companion, 'AUDIO_DUCKER', None), patch.object(companion, 'TEXT_MODE', True):
            self.assertIsNone(companion.audio_ducker())


if __name__ == '__main__':
    unittest.main()

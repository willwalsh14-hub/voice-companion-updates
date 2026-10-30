"""An expert screen reader user in Radio, Podcasts and YouTube (nightly sweep, October 30, 2026).

Someone who knows NVDA and JAWS well uses the keys fast and expects every key to do
exactly what it says, and the list to stay where it was. These tests drive the real app
through companion.handle and companion.handle_keyboard with a real MediaHub."""
import companion
from test_media_new_veteran import NewVeteranMediaTestCase


class ExpertMediaTestCase(NewVeteranMediaTestCase):
    def key(self, name):
        self.said.clear()
        request = companion.navigation_key(name, self.mode)
        if request:
            self.mode = companion.handle_keyboard(request, self.mode) or self.mode
        return ' '.join(self.said)


class PlayerControlsKeepTheirPlaceTests(ExpertMediaTestCase):
    """Space plays and pauses on the player controls list. It must not move you off the
    item you chose, or out of the controls list."""

    def test_space_on_radio_controls_keeps_the_chosen_control(self):
        self.say('Media', 'Radio', 'Search radio', 'WGN')
        self.key('Enter')
        self.say('Player controls')
        self.assertEqual(self.key('Down'), 'Louder (L), 2 of 5.')
        self.assertEqual(self.key('Space'), 'Audio paused.')
        nav = companion.MEDIA_NAV
        self.assertEqual((nav.view, nav.item_index), ('controls', 1))
        self.assertEqual(self.key('Down'), 'Quieter (Q), 3 of 5.')

    def test_space_on_podcast_controls_stays_on_the_controls(self):
        self.say('Media', 'Podcasts', 'Search podcasts for history')
        self.key('Enter')
        self.key('Down')
        self.assertTrue(self.key('Enter').startswith('Playing Greece. Player controls.'))
        self.key('Down')
        self.assertEqual(self.key('Space'), 'Audio paused.')
        nav = companion.MEDIA_NAV
        self.assertEqual((nav.view, nav.item_index), ('controls', 1))
        # Escape still goes back to the episodes, on the one playing, as the controls said.
        self.assertEqual(self.key('Escape'), 'Greece (G), 2 of 2.')


class EmptyListKeysTests(ExpertMediaTestCase):
    """Every key says something. In an empty results list, a letter used to say nothing."""

    def test_a_letter_in_an_empty_list_says_it_is_empty(self):
        self.say('Media', 'Podcasts')
        self.key('Tab')
        for name in ('Letter:p', 'Left', 'Right', 'Down'):
            with self.subTest(key=name):
                self.assertEqual(self.key(name), 'No items in this list. Tab returns to the actions menu.')


from contextlib import ExitStack
from unittest.mock import patch
import youtube_page as yp
import youtube_screen as ys
from test_youtube import NoBrowser


class _Page:
    url = 'https://www.youtube.com/'

    def is_closed(self):
        return False


def _videos(first, count):
    return [{'kind': 'video', 'id': 'v' + str(n), 'url': '', 'title': 'Video ' + str(n), 'channel': '', 'length': '',
             'views': '', 'age': ''} for n in range(first, first + count)]


class YouTubeNextPartFailsTests(NoBrowser):
    """The list said "more load as you move down". When YouTube does not send the next part,
    Down at the end must say so, not "End of list.", and arrows must not keep asking again."""

    def setUp(self):
        super().setUp()
        self.asked = []
        def refuse(page, token, search=False):
            self.asked.append(token)
            raise yp.YouTubeError(yp.NO_MORE)
        self.stack.enter_context(patch.object(yp.YouTubePage, 'next_part', refuse))
        self.s = ys.screen(self.app)
        self.s.list_page = _Page()
        self.s.push({'kind': 'list', 'name': 'home', 'title': 'Home', 'url': 'https://www.youtube.com/',
                     'items': _videos(1, 8), 'token': 'home-2', 'data': True})

    def test_the_end_of_a_list_that_could_not_load_more_says_why(self):
        for _ in range(7):
            self.press('Down')
        self.assertEqual(len(self.asked), 1)   # asked once near the end, not on every key
        self.press('Down')
        self.assertTrue(self.said[-1].startswith(yp.NO_MORE), self.said[-1])
        self.assertTrue(self.said[-1].endswith('8 of 8.'), self.said[-1])

    def test_a_list_with_no_next_part_still_says_end_of_list(self):
        self.s.view()['token'] = ''
        self.press('End'); self.press('Down')
        self.assertTrue(self.said[-1].startswith('End of list. Video 8'), self.said[-1])
        self.assertEqual(self.asked, [])


import unittest
from test_media_player_menu import MediaPlayerMenuTests as _PlayerTests


class MediaPlayerEndKeyTests(unittest.TestCase):
    """While a file plays, End said nothing. Like Tab, it now says how to reach the menu."""
    setUp, key, open_from_file_manager = _PlayerTests.setUp, _PlayerTests.key, _PlayerTests.open_from_file_manager

    def test_end_while_playing_is_never_silent(self):
        mode = self.open_from_file_manager()
        self.said.clear()
        self.assertEqual(self.key('End', mode), 'local_media')
        self.assertEqual(self.said[-1:], ['Press Alt for the player menu.'])
        self.assertFalse(companion.task_navigation().player_menu)
        self.assertFalse(self.player.paused)


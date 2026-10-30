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

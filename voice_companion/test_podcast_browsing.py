"""Browse podcasts: a list of categories, then each category's top podcasts (tester, October 2026).

"Browsing means looking through things, not having to come up with ideas myself."
Apple's charts are faked at the network boundary with the shapes Apple sends:
the category chart is Apple's old RSS-as-JSON feed, where every value is wrapped
in {"label": ...} and the show number is in id.attributes["im:id"]; the overall
chart is the newer feed with feed.results[].id; a lookup answers with
collectionId as a number and feedUrl.
"""
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from media_hub import (APPLE_LOOKUP, MediaHub, PODCAST_CATEGORIES, TOP_PODCASTS, chart_ids,
                       podcast_category)
from media_navigation import MediaNavigation, PODCASTS


def legacy_chart(*shows):
    return {'feed': {'author': {'name': {'label': 'iTunes Store'}},
                     'entry': [{'im:name': {'label': name},
                                'id': {'label': 'https://podcasts.apple.com/us/podcast/x/id' + number,
                                       'attributes': {'im:id': number}},
                                'category': {'attributes': {'im:id': '1303', 'term': 'Comedy', 'label': 'Comedy'}}}
                               for number, name in shows]}}


def lookup(*shows):
    return {'resultCount': len(shows), 'results': [
        dict({'wrapperType': 'track', 'kind': 'podcast', 'collectionId': int(number), 'collectionName': name,
              'primaryGenreName': 'Comedy'}, **({'feedUrl': 'https://feeds.example/' + number} if feed else {}))
        for number, name, feed in shows]}


class Network:
    """Answers like Apple, and records each address asked for."""
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, url):
        self.asked.append(url)
        for start, answer in self.answers:
            if url.startswith(start):
                if isinstance(answer, Exception):
                    raise answer
                return json.loads(json.dumps(answer))
        raise OSError('No route to ' + url)


class CategoryWordsTests(unittest.TestCase):
    def test_spoken_and_typed_names_find_the_category(self):
        for spoken, name in (('Comedy.', 'Comedy'), ('true crime', 'True crime'), ('True Crime podcasts', 'True crime'),
                             ('Health & Fitness', 'Health and fitness'), ('health', 'Health and fitness'),
                             ('Kids and family.', 'Kids and family'), ('religion', 'Religion and spirituality'),
                             ('TV and film', 'TV and film'), ('Top podcasts.', TOP_PODCASTS), ('the news', 'News')):
            with self.subTest(spoken=spoken):
                self.assertEqual(podcast_category(spoken), name)
        for other in ('', 'cooking', 'vintage cars', '...'):
            self.assertIsNone(podcast_category(other))

    def test_chart_numbers_from_both_chart_shapes_and_damaged_answers(self):
        self.assertEqual(chart_ids(legacy_chart(('11', 'A'), ('22', 'B'), ('11', 'A again'))), ['11', '22'])
        # One show: Apple sends the entry itself, not a list.
        one = legacy_chart(('33', 'C'));one['feed']['entry'] = one['feed']['entry'][0]
        self.assertEqual(chart_ids(one), ['33'])
        self.assertEqual(chart_ids({'feed': {'results': [{'id': '44', 'name': 'D'}, {'id': 'x'}, 'bad']}}), ['44'])
        for damaged in (None, [], {}, {'feed': []}, {'feed': {'entry': 'text'}}, {'feed': {'entry': [{'id': {'attributes': 'x'}}]}}):
            self.assertEqual(chart_ids(damaged), [])


class BrowseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.hub = MediaHub(self.temp.name, player=object())

    def test_a_category_lists_its_top_podcasts_in_chart_order_with_their_feeds(self):
        network = self.hub.fetch_json = Network([
            ('https://itunes.apple.com/us/rss/toppodcasts/', legacy_chart(('22', 'Second'), ('11', 'First'), ('33', 'Apple only'))),
            (APPLE_LOOKUP, lookup(('11', 'First', True), ('22', 'Second', True), ('33', 'Apple only', False)))])
        said = self.hub.command('browse podcasts category comedy', section='podcast')
        self.assertEqual(said, 'Comedy. 2 top podcasts. Podcast 1: Second. Say next or previous to browse. '
                               'Say that one to open episodes, or subscribe.')
        self.assertIn('/genre=1303/json', network.asked[0])
        self.assertEqual(parse_qs(urlsplit(network.asked[1]).query)['id'], ['22,11,33'])
        self.assertEqual([s['feed'] for s in self.hub.shows], ['https://feeds.example/22', 'https://feeds.example/11'])
        self.assertEqual(self.hub.shows_category, 'Comedy')
        self.assertIn('First', self.hub.command('next', section='podcast'))
        with patch.object(self.hub, '_feed_episodes', return_value=[{'title': 'Pilot', 'url': 'https://feeds.example/1.mp3', 'show': 'First'}]):
            self.assertIn('Pilot', self.hub.command('that one', section='podcast'))
        self.assertEqual(self.hub.current_feed, 'https://feeds.example/11')

    def test_top_podcasts_uses_the_overall_chart_then_apples_newer_chart(self):
        network = self.hub.fetch_json = Network([
            ('https://itunes.apple.com/us/rss/toppodcasts/', OSError('timed out')),
            ('https://rss.marketingtools.apple.com/', {'feed': {'results': [{'id': '55', 'name': 'Big show'}]}}),
            (APPLE_LOOKUP, lookup(('55', 'Big show', True)))])
        said = self.hub.command('Top podcasts.', section='podcast')
        self.assertTrue(said.startswith('Top podcasts. 1 top podcast. Podcast 1: Big show.'), said)
        self.assertNotIn('genre=', network.asked[0])

    def test_when_the_chart_fails_the_category_is_searched_within_apples_category(self):
        network = self.hub.fetch_json = Network([
            ('https://itunes.apple.com/us/rss/toppodcasts/', ValueError('not JSON')),
            ('https://itunes.apple.com/search', {'results': [{'collectionName': 'Old Times', 'collectionId': 9,
                                                              'feedUrl': 'https://feeds.example/old'}]})])
        self.assertIn('Podcast 1: Old Times', self.hub.command('browse podcasts category history', section='podcast'))
        query = parse_qs(urlsplit(network.asked[-1]).query)
        self.assertEqual((query['genreId'], query['term'], query['media']), (['1487'], ['History'], ['podcast']))

    def test_no_internet_says_so_plainly_and_keeps_the_old_list(self):
        self.hub.shows = [{'name': 'Kept', 'feed': 'https://feeds.example/kept'}];self.hub.show_index = 0
        self.hub.fetch_json = Network([])
        said = self.hub.command('browse podcasts category sports', section='podcast')
        self.assertEqual(said, 'The sports list is not available right now. Check the internet connection, then try again. '
                               'You can also search for a podcast by name.')
        self.assertEqual(self.hub.shows[0]['name'], 'Kept')
        self.assertIn('top podcasts list is not available', self.hub.command('top podcasts', section='podcast'))

    def test_a_chart_answer_of_the_wrong_shape_never_stops_the_app(self):
        for answer in ([], 'text', {'feed': None}, {'results': 'x'}):
            with self.subTest(answer=answer):
                self.hub.fetch_json = lambda url, answer=answer: answer
                self.assertIn('not available right now', self.hub.browse_category('News'))

    def test_a_word_that_is_not_a_category_searches_for_it_by_name(self):
        network = self.hub.fetch_json = Network([('https://itunes.apple.com/search', {'results': [
            {'collectionName': 'Car Talk', 'feedUrl': 'https://feeds.example/car'}]})])
        self.assertIn('Car Talk', self.hub.command('browse podcasts category vintage cars', section='podcast'))
        self.assertEqual(parse_qs(urlsplit(network.asked[0]).query)['term'], ['vintage cars'])

    def test_voice_browsing_moves_through_categories_and_opens_one(self):
        self.hub.fetch_json = Network([('https://itunes.apple.com/us/rss/toppodcasts/', legacy_chart(('7', 'Daily'))),
                                       (APPLE_LOOKUP, lookup(('7', 'Daily', True)))])
        said = self.hub.command('Browse podcasts.', section='podcast')
        self.assertTrue(said.startswith('Browse podcasts. Choose a category to hear its top podcasts. Top podcasts (T), 1 of 17.'), said)
        self.assertEqual(self.hub.command('previous', section='podcast'), 'That is the first category.')
        self.assertEqual(self.hub.command('next', section='podcast'), 'News (N), 2 of 17. Say that one to open it.')
        self.assertTrue(self.hub.command('that one', section='podcast').startswith('News. 1 top podcast. Podcast 1: Daily.'))
        self.assertFalse(self.hub.browsing_categories)
        self.assertIn('News (N), 2 of 17', self.hub.command('back to categories', section='podcast'))
        self.assertTrue(self.hub.command('comedy', section='podcast').startswith('Comedy.'))

    def test_search_still_searches_and_leaves_category_browsing(self):
        self.hub.command('browse podcasts', section='podcast')
        self.hub.fetch_json = lambda url: {'results': [{'collectionName': 'Found', 'feedUrl': 'https://feeds.example/f'}]}
        self.assertIn('Found', self.hub.command('search podcasts for found', section='podcast'))
        self.assertFalse(self.hub.browsing_categories)
        self.assertIsNone(self.hub.shows_category)


class BrowseScreenTests(unittest.TestCase):
    """The Podcasts screen, by keyboard: Browse podcasts is a list, not a question."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.hub = MediaHub(self.temp.name, player=object())
        self.hub.fetch_json = Network([
            ('https://itunes.apple.com/us/rss/toppodcasts/', legacy_chart(('1', 'Laughs'), ('2', 'Giggles'))),
            (APPLE_LOOKUP, lookup(('1', 'Laughs', True), ('2', 'Giggles', True)))])
        self.nav = MediaNavigation(self.hub, 'podcast')
        self.nav.enter('podcast', keyboard=True)

    def test_menu_names_say_what_they_do(self):
        labels = [a.label for a in PODCASTS]
        self.assertEqual(labels[:3], ['Browse podcasts', 'Top podcasts', 'Search podcasts'])
        self.assertNotIn('Browse podcast category', labels)
        browse = PODCASTS[0]
        self.assertEqual((browse.command, browse.form), ('browse podcasts', ''))
        self.assertEqual(PODCASTS[2].form, 'search')

    def test_enter_on_browse_lists_categories_then_a_category_lists_its_shows(self):
        said = self.nav.keyboard('Enter')
        self.assertIn('Top podcasts (T), 1 of 17', said)
        self.assertEqual((self.nav.view, self.nav.focus), ('categories', 'items'))
        self.assertEqual(self.nav.keyboard('Down'), 'News (N), 2 of 17.')
        self.assertEqual(self.nav.keyboard('Letter:c'), 'Comedy (C), 3 of 17.')
        self.assertTrue(self.nav.keyboard('Enter').startswith('Comedy. 2 top podcasts. Podcast 1: Laughs.'))
        self.assertEqual((self.nav.view, self.nav.focus), ('shows', 'items'))
        self.assertEqual(self.nav.keyboard('Down'), 'Giggles (G), 2 of 2.')
        # Escape goes back up one level, to the categories, on the one that was chosen.
        self.assertIn('Comedy (C), 3 of 17', self.nav.keyboard('Escape'))
        self.assertEqual(self.nav.view, 'categories')
        self.assertEqual(self.nav.keyboard('Escape'), 'Browse podcasts (B), 1 of ' + str(len(PODCASTS)) + '.')
        self.assertEqual(self.nav.focus, 'actions')

    def test_categories_name_every_one_the_tester_asked_for(self):
        self.nav.keyboard('Enter')
        names = [item['name'] for item in self.nav.items()]
        for wanted in ('News', 'Comedy', 'True crime', 'Sports', 'History', 'Health and fitness', 'Business', 'Education',
                       'Religion and spirituality', 'Kids and family', 'Music', 'Society and culture', 'Technology', 'Arts'):
            self.assertIn(wanted, names)
        self.assertEqual(names[0], TOP_PODCASTS)
        self.assertEqual(len(names), len(PODCAST_CATEGORIES) + 1)

    def test_top_podcasts_action_lists_the_chart(self):
        self.nav.keyboard('Down')
        self.assertEqual(self.nav.announce(), 'Top podcasts (T), 2 of ' + str(len(PODCASTS)) + '.')
        self.assertTrue(self.nav.keyboard('Enter').startswith('Top podcasts. 2 top podcasts.'))
        self.assertEqual((self.nav.view, self.nav.focus), ('shows', 'items'))

    def test_escape_from_search_results_still_goes_to_the_actions(self):
        self.hub.fetch_json = lambda url: {'results': [{'collectionName': 'Found', 'feedUrl': 'https://feeds.example/f'}]}
        self.nav.command('search podcasts for found')
        self.assertEqual(self.nav.view, 'shows')
        self.nav.keyboard('Escape')
        self.assertEqual(self.nav.focus, 'actions')

    def test_voice_on_the_screen_and_from_the_main_menu(self):
        import companion
        with patch.object(companion, 'MEDIA_HUB', self.hub), patch.object(companion, 'MEDIA_NAV', None), patch.object(companion, 'MEDIA_SECTION', 'radio'), \
             patch.object(companion, 'speak') as speech:
            for phrase in ('Browse podcasts.', 'top podcasts'):
                with self.subTest(phrase=phrase):
                    self.assertEqual(companion.handle(phrase, 'awake'), 'media')
                    self.assertTrue(speech.call_args.args[0].startswith(('Browse podcasts.', 'Top podcasts.')), speech.call_args.args[0])


if __name__ == '__main__':
    unittest.main()

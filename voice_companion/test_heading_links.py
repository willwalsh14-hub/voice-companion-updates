"""A heading that is a link says so after its level, as JAWS does (Will, October 2026).

Search results put the link around the heading (Google) or inside it. Both browsers use
the same reader, so this covers Chrome and the built-in browser."""
import unittest

from web_browse import BrowseCursor, describe, heading_link


def page(*elements):
    return BrowseCursor({'elements': list(elements)})


HEADING = {'role': 'heading', 'level': 3, 'label': 'Freedom Scientific JAWS downloads', 'start': 10, 'end': 43}


class HeadingLinkTests(unittest.TestCase):
    def test_link_around_the_heading(self):
        link = {'role': 'link', 'label': 'Freedom Scientific JAWS downloads', 'start': 10, 'end': 70}
        cursor = page({'role': 'heading', 'level': 1, 'label': 'Results', 'start': 0, 'end': 7}, link, dict(HEADING))
        cursor.move('h')
        self.assertEqual(cursor.move('h'), 'Freedom Scientific JAWS downloads, heading level 3, link.')

    def test_link_inside_the_heading(self):
        link = {'role': 'link', 'label': 'JAWS downloads', 'start': 30, 'end': 43}
        cursor = page(dict(HEADING), link)
        self.assertEqual(cursor.move('h'), 'Freedom Scientific JAWS downloads, heading level 3, link.')

    def test_plain_heading_is_unchanged(self):
        later = {'role': 'link', 'label': 'More', 'start': 50, 'end': 54}
        self.assertEqual(describe(HEADING, [HEADING, later]), 'Freedom Scientific JAWS downloads, heading level 3.')
        self.assertFalse(heading_link(HEADING, [HEADING, later]))

    def test_heading_level_keys_also_say_link(self):
        link = {'role': 'link', 'label': 'x', 'start': 10, 'end': 43}
        self.assertEqual(page(dict(HEADING), link).move('3'), 'Freedom Scientific JAWS downloads, heading level 3, link.')


if __name__ == '__main__':
    unittest.main()

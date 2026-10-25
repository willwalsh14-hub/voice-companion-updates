"""The HTML user guide meets WCAG 2.2 AA structure rules (Will, October 2026).

Screen reader users move by headings, landmarks, lists and links, so those must be right."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

GUIDE = Path(__file__).with_name('START HERE - Veteran.html')
VOID = {'meta', 'br', 'hr', 'img', 'link', 'input', 'source', 'wbr', 'col', 'area', 'base', 'embed', 'track'}


class Structure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.headings, self.ids, self.links, self.tags = [], [], [], set(), [], []
        self.lang = None; self.title = ''; self._in_title = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs); self.tags.append(tag)
        if tag == 'html': self.lang = attrs.get('lang')
        if tag == 'title': self._in_title = True
        if attrs.get('id'):
            if attrs['id'] in self.ids: self.errors.append('duplicate id ' + attrs['id'])
            self.ids.add(attrs['id'])
        if tag == 'a': self.links.append(attrs.get('href', ''))
        if re.fullmatch(r'h[1-6]', tag): self.headings.append(int(tag[1]))
        if tag == 'li' and (not self.stack or self.stack[-1] not in ('ul', 'ol')):
            self.errors.append('list item outside a list')
        if tag not in VOID: self.stack.append(tag)
    def handle_endtag(self, tag):
        if tag == 'title': self._in_title = False
        if tag in VOID: return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append('mismatched </' + tag + '> after <' + (self.stack[-1] if self.stack else '') + '>'); return
        self.stack.pop()
    def handle_data(self, data):
        if self._in_title: self.title += data


class GuideHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = GUIDE.read_text(encoding='utf-8')
        cls.page = Structure(); cls.page.feed(cls.html); cls.page.close()

    def test_well_formed_with_language_and_title(self):
        self.assertTrue(self.html.lstrip().lower().startswith('<!doctype html>'))
        self.assertEqual(self.page.errors, [])
        self.assertEqual(self.page.stack, [])
        self.assertTrue(self.page.lang and self.page.lang.lower().startswith('en'))
        self.assertTrue(self.page.title.strip())

    def test_one_h1_and_no_skipped_heading_levels(self):
        levels = self.page.headings
        self.assertEqual(levels.count(1), 1); self.assertEqual(levels[0], 1)
        self.assertEqual([(a, b) for a, b in zip(levels, levels[1:]) if b > a + 1], [])

    def test_landmarks_skip_link_and_contents(self):
        for tag in ('header', 'nav', 'main'):
            self.assertIn(tag, self.page.tags)
        self.assertEqual(self.page.tags.count('main'), 1)
        self.assertEqual(self.page.links[0], '#main')
        self.assertIn('main', self.page.ids)

    def test_every_page_link_has_a_target_and_a_label(self):
        missing = [href for href in self.page.links if href.startswith('#') and href[1:] not in self.page.ids]
        self.assertEqual(missing, [])
        self.assertIsNone(re.search(r'>\s*(click here|here|link)\s*</a>', self.html, re.I))

    def test_no_tables_and_visible_focus(self):
        self.assertNotIn('<table', self.html)
        self.assertIn(':focus-visible', self.html)
        self.assertIn('width=device-width', self.html)


if __name__ == '__main__':
    unittest.main()

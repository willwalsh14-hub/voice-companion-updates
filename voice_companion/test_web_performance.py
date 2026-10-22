"""Busy pages (video sites) must not stall Voice Companion.

These checks count work instead of timing it where they can: label lookups,
characters rescanned, page reads per minute. Where a time is checked, the
limit is far from both the old and the new behaviour.
"""
import tempfile
import time
import unittest

from test_web_assistant import FakeBackend
from web_assistant import (BrowserBackend, RefreshPacer, SKIP_AD_READY, WebSession,
                           skip_ad_button)


class PacingTests(unittest.TestCase):
    def test_quick_checks_keep_the_short_interval(self):
        pacer = RefreshPacer()
        self.assertTrue(pacer.due(10.0))
        pacer.done(10.0, 10.01)
        self.assertFalse(pacer.due(10.2))
        self.assertTrue(pacer.due(10.31))
        # Right after a key press the check waits a moment, as before.
        self.assertFalse(pacer.due(10.4, last_navigation=10.35))
        self.assertTrue(pacer.due(10.5, last_navigation=10.35))

    def test_slow_page_reads_leave_room_for_keys_and_speech(self):
        pacer = RefreshPacer()
        pacer.done(10.0, 10.5)
        self.assertFalse(pacer.due(11.9))
        self.assertTrue(pacer.due(12.01))
        self.assertFalse(pacer.due(12.5, last_navigation=12.2))
        self.assertTrue(pacer.due(13.21, last_navigation=12.2))

    def test_waits_never_grow_past_two_seconds(self):
        pacer = RefreshPacer()
        pacer.done(0.0, 5.0)
        self.assertTrue(pacer.due(7.01))
        self.assertTrue(pacer.due(7.01, last_navigation=5.0))

    def test_constantly_changing_page_gets_a_bounded_share_of_the_main_loop(self):
        # Each read of the page costs half a second, and the page always changes.
        clock, busy, reads, pacer = 0.0, 0.0, 0, RefreshPacer()
        while clock < 60:
            if pacer.due(clock):
                started = clock; clock += .5; busy += .5; reads += 1
                pacer.done(started, clock)
            else:
                clock += .02
        self.assertLess(busy / 60, .3)
        self.assertGreater(reads, 20)  # The view still updates every two seconds or so.

    def test_main_loop_uses_the_pacer(self):
        from pathlib import Path
        source = (Path(__file__).parent / 'companion.py').read_text(encoding='utf-8')
        self.assertIn('web_refresh.due(', source)
        self.assertIn('web_refresh.done(', source)


class FrameChurnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)

    def test_ad_frames_that_come_and_go_do_not_wait_for_the_page_to_settle(self):
        backend = FakeBackend(); backend.waits = 0; backend.calls = 0
        def stamp():
            backend.calls += 1
            return {'url': backend.url, 'epoch': 1, 'revision': 0, 'ready': 'complete', 'busy': False, 'frames': [],
                    'frame_identity': [('https://ads.example/ad', str(backend.calls))]}
        backend.document_stamp = stamp
        backend.wait_ready = lambda: setattr(backend, 'waits', backend.waits + 1)
        session = WebSession(self.temp.name, backend)
        session._refresh(wait=False)
        self.assertEqual(backend.waits, 0)
        self.assertIn('Welcome', session.reading.text)

    def test_main_page_navigation_during_capture_still_waits(self):
        backend = FakeBackend(); backend.waits = 0; backend.epoch = 1; backend.calls = 0
        backend.document_stamp = lambda: {'url': backend.url, 'epoch': backend.epoch}
        original = backend.snapshot
        def snapshot():
            backend.calls += 1
            if backend.calls == 1: backend.epoch = 2
            return original()
        backend.snapshot = snapshot
        backend.wait_ready = lambda: setattr(backend, 'waits', backend.waits + 1)
        WebSession(self.temp.name, backend)._refresh(wait=False)
        self.assertEqual(backend.waits, 1)


class SkipAdFakeBackend(FakeBackend):
    def __init__(self):
        super().__init__(); self.url = 'https://video.example/watch'; self.ad = False; self.pressed = []; self.revision = 0
    def document_stamp(self):
        return {'url': self.url, 'epoch': 1, 'revision': self.revision, 'ready': 'complete', 'busy': False, 'frames': []}
    def snapshot(self):
        result = super().snapshot()
        result['elements'] = [{'key': 'p1', 'role': 'paragraph', 'label': 'Video', 'start': 0, 'end': 5, 'tag': 'p'}]
        if self.ad:
            result['text'] += ' Skip ad, button'
            result['elements'].append({'key': 'n9', 'role': 'button', 'label': 'Skip Ad', 'tag': 'button', 'type': 'submit',
                                       'start': len(result['text']) - 16, 'end': len(result['text'])})
        return result
    def activate(self, item):
        self.pressed.append(item['key']); self.ad = False; self.revision += 1
        return {'fragment': None, 'frame_index': None}


class SkipAdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.backend = SkipAdFakeBackend(); self.session = WebSession(self.temp.name, self.backend)
        self.session.auto_read = False; self.session._refresh(read=False)

    def test_skip_buttons_are_recognised_by_their_words(self):
        page = lambda url, label, **extra: {'url': url, 'elements': [dict(role='button', label=label, tag='button', **extra)]}
        for url, label in (('https://video.example/', 'Skip Ad'), ('https://video.example/', 'Skip ads.'),
                           ('https://www.youtube.com/watch?v=1', 'Skip'), ('https://m.youtube.com/watch', 'skip ad')):
            self.assertIsNotNone(skip_ad_button(page(url, label)), label)
        for url, label in (('https://news.example/', 'Skip'), ('https://news.example/', 'Skip to main content'),
                           ('https://www.youtube.com/', 'Skip navigation')):
            self.assertIsNone(skip_ad_button(page(url, label)), label)
        self.assertIsNone(skip_ad_button(page('https://video.example/', 'Skip ad', disabled=True)))
        self.assertIsNone(skip_ad_button({'url': 'https://video.example/', 'elements': [{'role': 'link', 'label': 'Skip ad', 'tag': 'a'}]}))

    def test_skip_button_is_announced_once_when_it_appears(self):
        self.assertEqual(self.session.refresh_changed(), '')
        self.backend.ad = True; self.backend.revision += 1
        self.assertEqual(self.session.refresh_changed(), SKIP_AD_READY)
        self.backend.revision += 1
        self.assertEqual(self.session.refresh_changed(), '')

    def test_saying_skip_ad_presses_it_without_moving_the_reading_place(self):
        self.backend.ad = True; self.backend.revision += 1; self.session.refresh_changed()
        position = self.session.reading.position
        self.assertEqual(self.session.command('Skip ad.'), 'Ad skipped.')
        self.assertEqual(self.backend.pressed, ['n9'])
        self.assertEqual(self.session.reading.position, position)
        self.assertEqual(self.session.command('skip the ad'), 'No Skip ad button is showing now.')

    def test_skip_ad_is_text_while_writing_in_a_field(self):
        self.backend.ad = True; self.backend.revision += 1; self.session.refresh_changed()
        field = next(c for c in self.backend.controls if c['label'] == 'Full name')
        self.session.keyboard_field = dict(field, _page_url=self.backend.url)
        try: self.session.command('skip ad')
        except Exception: pass
        self.assertEqual(self.backend.pressed, [])


BIG_LABELS = ('<label for="name">Full name</label><input id="name"><label><input type="checkbox">Agree</label>'
              '<div id="host"></div>' + ''.join('<p>Comment %d <button>Like %d</button></p>' % (i, i) for i in range(300))
              + '<script>document.getElementById("host").attachShadow({mode:"open"}).innerHTML='
                '\'<label for="inner">Shadow field</label><input id="inner">\'</script>')
COUNT_LABELS = '''()=>{window.labelReads=0;window.labelGetters={};for(const name of ['HTMLButtonElement','HTMLInputElement','HTMLSelectElement','HTMLTextAreaElement']){
  const proto=window[name].prototype,original=Object.getOwnPropertyDescriptor(proto,'labels');
  const get=function(){window.labelReads++;return original.get.call(this);};window.labelGetters[name]=get;
  Object.defineProperty(proto,'labels',{configurable:true,enumerable:original.enumerable,get});}}'''
COUNT_SCANS = '''()=>{window.scanned=0;const big=s=>{s=String(s);if(s.length>2000)window.scanned+=s.length;};
  const replace=String.prototype.replace,test=RegExp.prototype.test,ends=String.prototype.endsWith;
  window.restoreScans=()=>{String.prototype.replace=replace;RegExp.prototype.test=test;String.prototype.endsWith=ends;};
  String.prototype.replace=function(...a){big(this);return replace.apply(this,a);};
  RegExp.prototype.test=function(s){big(s);return test.call(this,s);};
  String.prototype.endsWith=function(...a){big(this);return ends.apply(this,a);};}'''
MOTION = '''<div id="bar" style="width:0%;height:3px"></div><div id="slider" role="slider" aria-label="Seek" aria-valuenow="0" tabindex="0"></div>
<p id="menu" style="display:none">Menu</p><p>Video</p><script>let t=0;setInterval(()=>{t++;const bar=document.getElementById('bar');
bar.style.width=(t%100)+'%';bar.style.transform='scaleX('+(t%10)/10+')';},15);</script>'''
SLIDER = '''<script>let v=0;setInterval(()=>document.getElementById('slider').setAttribute('aria-valuenow',String(++v)),40);</script>'''


class BusyPageTests(unittest.TestCase):
    """Real browser checks. test_webview2 runs them again in embedded Edge."""
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls.driver = sync_playwright().start()
            cls.browser = cls.driver.chromium.launch()
        except Exception as error:
            if getattr(cls, 'driver', None): cls.driver.stop()
            raise unittest.SkipTest('Chromium is unavailable: ' + str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.driver.stop()

    def setUp(self):
        self.context = getattr(self, 'persistent_context', None) or self.browser.new_context()
        if hasattr(self, 'persistent_context'): self.addCleanup(lambda: self.context.unroute_all(behavior='wait'))
        else: self.addCleanup(self.context.close)
        self.page = self.context.new_page(); self.page.set_default_timeout(10000)
        if hasattr(self, 'persistent_context'): self.addCleanup(self.page.close)
        self.backend = BrowserBackend(tempfile.gettempdir()); self.backend.page = self.page
        self.backend.context = self.context; self.backend.start = lambda: None
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.session = WebSession(self.temp.name, self.backend); self.session.auto_read = False

    def load(self, html):
        self.page.set_content(html); self.session._refresh(read=False)

    def test_labels_are_looked_up_once_per_snapshot_not_once_per_button(self):
        self.page.set_content(BIG_LABELS); self.page.evaluate(COUNT_LABELS)
        snapshot = self.backend.snapshot()
        self.assertLessEqual(self.page.evaluate('window.labelReads'), 2)
        labels = {c['label'] for c in snapshot['controls']}
        for expected in ('Full name', 'Agree', 'Shadow field', 'Like 299'):
            self.assertIn(expected, labels)
        # The page's own definitions are back in place.
        self.assertTrue(self.page.evaluate("Object.getOwnPropertyDescriptor(HTMLButtonElement.prototype,'labels').get===window.labelGetters.HTMLButtonElement"))
        self.assertEqual(self.page.evaluate("document.getElementById('name').labels.length"), 1)

    def test_page_text_is_assembled_without_rescanning_it(self):
        self.page.set_content(''.join('<p>Paragraph number %d has a few words.</p> <div>Note %d <span>more</span></div>' % (i, i) for i in range(1500)))
        self.page.evaluate(COUNT_SCANS)
        try: snapshot = self.backend.snapshot()
        finally: self.page.evaluate('window.restoreScans()')
        self.assertGreater(len(snapshot['text']), 50000)
        self.assertLess(self.page.evaluate('window.scanned'), 5 * len(snapshot['text']))

    def test_snapshot_arrives_as_one_text(self):
        self.page.set_content(BIG_LABELS)
        results = []; evaluate = self.page.evaluate
        def recording(script, *args, **kwargs):
            value = evaluate(script, *args, **kwargs); results.append((len(script), value)); return value
        self.page.evaluate = recording
        try: snapshot = self.backend.snapshot()
        finally: del self.page.evaluate
        largest = max(results, key=lambda item: item[0])[1]
        self.assertIsInstance(largest, str)
        self.assertIn('Like 0', snapshot['text'])

    def test_moving_progress_bar_is_not_a_page_change(self):
        self.load(MOTION)
        first = self.backend.document_stamp(); self.page.wait_for_timeout(400)
        second = self.backend.document_stamp()
        self.assertEqual(first['revision'], second['revision'])
        self.page.evaluate("document.getElementById('menu').style.display='block'")
        self.assertGreater(self.backend.document_stamp()['revision'], second['revision'])
        self.session.refresh_changed()
        self.assertIn('Menu', self.session.reading.text)

    def test_slider_values_update_but_do_not_keep_the_page_unsettled(self):
        self.load(MOTION + SLIDER)
        first = self.backend.document_stamp(); self.page.wait_for_timeout(400)
        second = self.backend.document_stamp()
        self.assertGreater(second['revision'], first['revision'])
        self.assertGreaterEqual(second['age'], 300)
        started = time.monotonic(); self.backend.wait_ready()
        # The old check waited its full four seconds on such a page.
        self.assertLess(time.monotonic() - started, 3)

    def test_button_that_removes_itself_does_not_wait_for_a_timeout(self):
        self.load('<p id="state">Banner shown</p><button onclick="this.remove();document.getElementById(\'state\').textContent=\'Banner closed\'">Close banner</button>')
        item = next(e for e in self.session.browse.elements if e.get('label') == 'Close banner')
        self.session.browse.index = self.session.browse.elements.index(item)
        started = time.monotonic(); result = self.session.keyboard('Activate')
        self.assertLess(time.monotonic() - started, 5)  # Was over ten seconds.
        self.assertNotIn('could not be used', result)
        self.assertIn('Banner closed', self.session.reading.text)

    def test_skip_ad_in_a_shadow_root_is_announced_and_pressed(self):
        # The player's controls live in a shadow root; the button arrives later.
        self.load('''<div id="player"><p>Video playing</p><div id="ad"></div></div><p>Comments</p>
        <script>window.skips=0;const root=document.getElementById('ad').attachShadow({mode:'open'});root.innerHTML='<span>Ad</span>';
        setTimeout(()=>{root.innerHTML='<span>Ad</span><button>Skip Ad</button>';root.querySelector('button').onclick=()=>{window.skips++;document.getElementById('ad').remove();};},300);</script>''')
        self.page.wait_for_function("!!document.getElementById('ad').shadowRoot.querySelector('button')")
        self.assertEqual(self.session.refresh_changed(), SKIP_AD_READY)
        started = time.monotonic()
        self.assertEqual(self.session.command('Skip ad.'), 'Ad skipped.')
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.page.evaluate('window.skips'), 1)
        self.assertIn('Comments', self.session.reading.text)


if __name__ == '__main__':
    unittest.main()

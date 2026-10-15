"""Real-browser checks for standard names, structures, typed lists, and HTTP policy."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
import test_web_live as live_helpers
import test_consumer_web as consumer_helpers
from test_web_assistant import FakeBackend
from web_assistant import WebSession, WebError
from web_browse import kind
from browser_navigation import prepare_address
from settings_model import DEFAULTS,fields


class WebPolicyTests(unittest.TestCase):
    def test_http_defaults_saves_and_private_values_stay_protected(self):
        self.assertFalse(DEFAULTS['web_block_http'])
        self.assertTrue(any(f.key=='web_block_http' for f in fields('Web browser',{})))
        self.assertEqual(prepare_address('http://example.com:8080/Case?q=One')[0],'http://example.com:8080/Case?q=One')
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend())
            session.open('http://example.com/Case');self.assertTrue(session.navigation_succeeded)
            session.favorites.add('Old site',session.snapshot['url'])
            self.assertEqual(session.favorites.get('Old site')['url'],'http://example.com/Case')
            self.assertEqual(session.menu.history()[0]['url'],'http://example.com/Case')
            with self.assertRaises(WebError):session.favorites.add('Private','http://example.com/?access_token=private')
            session.set_block_http(True)
            self.assertIn('blocked',session.open('http://example.com/Case'));self.assertFalse(session.navigation_succeeded)
            (Path(folder)/'preferences.json').write_text(json.dumps({'web_block_http':True}))
            self.assertTrue(WebSession(folder,FakeBackend()).block_http)

    def test_links_list_prefix_full_names_repeated_letters_and_backspace(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=FakeBackend();backend.controls=[dict(key=str(i),role='link',tag='a',type='',label=n,href='http://example.com/'+str(i)) for i,n in enumerate(['Alpha','Account settings','Beta','Bravo'])]
            session=WebSession(folder,backend);session._refresh();session.list_links()
            now=100
            for char in 'Account settings':
                session.list_name_input(char,now=now);now+=.01
            self.assertEqual(session.link_position,1);self.assertEqual(backend.activated,[])
            self.assertIn('No item',session.list_name_input('z',now=now))
            self.assertIn('Account settings',session.list_name_input('',backspace=True,now=now+.1))
            self.assertIn('Beta',session.list_name_input('b',now=200))
            self.assertIn('Bravo',session.list_name_input('b',now=200.1))
            self.assertEqual(session.list_name_input('b',now=200.2),'End of matching items.')
            session.activate_list_item();self.assertEqual(backend.activated,['3'])

    def test_accessible_name_bundle_and_license_are_packaged_and_unchanged(self):
        root=Path(__file__).parent;record=json.loads((root/'accessible_names.provenance.json').read_text())
        self.assertEqual(hashlib.sha256((root/'accessible_names.js').read_bytes()).hexdigest(),record['bundle_sha256'])
        build=(root/'build-windows.ps1').read_text()
        for name in ('accessible_names.js','accessible_names.LICENSE.txt'):self.assertIn("'"+name+":.'",build)
        self.assertIn('MIT',(root/'accessible_names.LICENSE.txt').read_text())


class SemanticWebTests(unittest.TestCase):
    setUpClass=classmethod(live_helpers.LiveWebTests.setUpClass.__func__)
    tearDownClass=classmethod(live_helpers.LiveWebTests.tearDownClass.__func__)
    setUp=live_helpers.LiveWebTests.setUp

    def load(self,markup):
        self.page.set_content('<html><title>Semantic page</title><body>'+markup+'</body></html>')
        self.session._refresh()

    def test_names_use_labelledby_before_label_and_keep_author_names(self):
        self.load('<span id="first" hidden>Account</span><span id="last">settings</span><button aria-labelledby="first last" aria-label="Wrong name">Go</button><h1 aria-label="Section name">Visible title</h1><a href="http://fixture.test/next" aria-label="Open account">Different visible text</a>')
        elements=self.session.snapshot['elements']
        self.assertIn('Account settings',[e['label'] for e in elements if kind(e)=='button'])
        self.page.locator('button').focus()
        self.assertEqual(self.session.backend.focused_control()['label'],'Account settings')
        self.assertIn('Section name',self.session.keyboard('Quick:h'))
        self.assertIn('Open account',self.session.keyboard('Quick:k'))

    def test_graphics_native_aria_svg_and_decorative_exclusions(self):
        self.load('<img alt="Company logo"><img alt="" src="decorative.png"><div role="img" aria-label="Sales chart"><span>Internal chart labels</span></div><svg><title>Revenue diagram</title><circle r="5"/></svg><img src="missing-name.png">')
        labels=[e['label'] for e in self.session.snapshot['elements'] if kind(e)=='graphic']
        self.assertEqual(labels[:3],['Company logo','Sales chart','Revenue diagram'])
        self.assertIn('Unlabeled',labels[-1])
        self.assertNotIn('Internal chart labels',self.session.reading.text)
        for label in labels:self.assertIn(label,self.session.command('next graphic'))
        self.assertIn(labels[-2],self.session.command('previous image'))

    def test_lists_list_items_nested_counts_and_presentation(self):
        self.load('<ul aria-label="Shopping"><li>Milk</li><li>Fruit<ul><li>Apples</li><li>Oranges</li></ul></li></ul><div role="list" aria-label="Results"><div role="listitem" aria-posinset="7" aria-setsize="20">Article seven</div></div><ul role="presentation"><li>Plain text</li></ul>')
        self.assertIn('Shopping',self.session.command('next list'))
        self.assertEqual(self.session.browse.current()['item_count'],2)
        self.assertIn('Milk',self.session.keyboard('Quick:i'))
        self.assertIn('Fruit',self.session.command('next list item'))
        self.assertNotIn('Apples',self.session.browse.current()['label'])
        self.assertIn('2 items',self.session.keyboard('Quick:l'))
        self.assertIn('Results',self.session.keyboard('Quick:l'))
        self.assertIn('7 of 20',self.session.keyboard('Quick:i'))
        self.assertNotIn('Plain text',[e['label'] for e in self.session.snapshot['elements'] if kind(e)=='listitem'])

    def test_named_regions_and_native_landmarks_keep_their_types(self):
        self.load('<header>Top</header><nav aria-label="Primary">Links</nav><main><section aria-labelledby="news"><h2 id="news">News</h2><header>Nested heading area</header><p>News text</p></section><section><p>Ordinary section</p></section><div role="region" aria-label="Weather">Sunny</div></main><footer>Bottom</footer>')
        landmarks=[e for e in self.session.snapshot['elements'] if kind(e)=='landmark']
        self.assertEqual([e['landmark'] for e in landmarks],['banner','navigation','main','region','region','contentinfo'])
        self.assertIn('Primary, navigation region',self.session.command('next region')+' '+self.session.command('next landmark'))
        self.assertIn('News',[e['label'] for e in landmarks])

    def test_owned_list_items_and_hidden_slotted_controls_follow_page_semantics(self):
        self.load('<div role="list" aria-label="Owned results" aria-owns="owned"></div><span id="owned" role="listitem">Owned article</span><div id="component"></div><div id="hidden" aria-hidden="true"></div>')
        self.page.evaluate("""()=>{document.querySelector('#component').attachShadow({mode:'open'}).innerHTML='<ul aria-label="Slotted list"><slot></slot></ul>';document.querySelector('#component').innerHTML='<li>Projected item</li>';document.querySelector('#hidden').attachShadow({mode:'open'}).innerHTML='<button>Hidden control</button>';}""")
        self.session._refresh()
        self.assertIn('Owned results',self.session.keyboard('Quick:l'))
        self.assertEqual(self.session.browse.current()['item_count'],1)
        self.assertIn('Owned article',self.session.keyboard('Quick:i'))
        self.assertIn('Slotted list',self.session.keyboard('Quick:l'))
        self.assertEqual(self.session.browse.current()['item_count'],1)
        self.assertIn('Projected item',self.session.keyboard('Quick:i'))
        self.assertNotIn('Hidden control',str(self.session.snapshot))

    def test_named_control_states_and_range_roles_are_identified(self):
        self.load('<button aria-expanded="false" aria-pressed="true">Details</button><div role="switch" aria-label="Notifications" aria-checked="true"></div><label>Volume<input type="range" value="25"></label><div role="progressbar" aria-label="Download" aria-valuenow="40" aria-valuetext="40 percent"></div>')
        from web_browse import describe
        notices=[describe(e) for e in self.session.snapshot['elements']]
        self.assertTrue(any('Details' in n and 'collapsed' in n and 'pressed' in n for n in notices))
        self.assertIn('Notifications, switch, on.',notices)
        self.assertIn('Volume, slider, 25.',notices)
        self.assertIn('Download, progressbar, 40 percent.',notices)

    def test_visible_nested_cross_origin_frames_keep_document_order_and_names(self):
        self.context.route('https://fixture.test/semantic-frame',lambda r:r.fulfill(content_type='text/html',body='<p>Inside frame</p><iframe title="Nested frame" src="https://other.test/nested"></iframe>'))
        self.context.route('https://other.test/**',lambda r:r.fulfill(content_type='text/html',body='<p>Nested content</p>'))
        self.load('<p>Before frame</p><iframe title="Account details" src="https://fixture.test/semantic-frame"></iframe><p>After frame</p><iframe hidden title="Hidden frame" src="https://other.test/hidden"></iframe>')
        text=self.session.reading.text
        self.assertLess(text.index('Before frame'),text.index('Inside frame'))
        self.assertLess(text.index('Inside frame'),text.index('Nested content'))
        self.assertLess(text.index('Nested content'),text.index('After frame'))
        self.assertNotIn('Hidden frame',text)
        self.assertIn('Account details',self.session.command('next frame'))
        self.assertIn('Nested frame',self.session.keyboard('Quick:m'))
        self.assertIn('Account details',self.session.keyboard('Quick:Previous:m'))
        self.assertIn('End of frame Account details',text)

    def test_http_navigation_and_redirect_policy_can_be_enabled_and_disabled(self):
        reached=[]
        self.context.route('http://fixture.test/**',lambda r:(reached.append(r.request.url),r.fulfill(content_type='text/html',body='<title>HTTP page</title><p>HTTP content</p>'))[-1])
        self.context.route('https://fixture.test/downgrade',lambda r:r.fulfill(status=302,headers={'location':'http://fixture.test/destination'}))
        self.session.open('http://fixture.test/page');self.assertEqual(self.page.url,'http://fixture.test/page')
        self.assertIn('HTTP content',self.session.reading.text)
        self.session.set_block_http(True);reached.clear()
        self.assertIn('blocked',self.session.open('https://fixture.test/downgrade'))
        self.assertEqual(reached,[])
        self.session.set_block_http(False)
        self.session.open('https://fixture.test/downgrade');self.assertEqual(self.page.url,'http://fixture.test/destination')


@unittest.skipUnless(sys.platform=='win32','Windows Chrome semantic checks')
class ChromeSemanticWebTests(SemanticWebTests):
    browser_name='chrome'
    setUpClass=classmethod(consumer_helpers.ChromeConsumerWebTests.setUpClass.__func__)

@unittest.skipUnless(sys.platform=='win32','Windows Edge semantic checks')
class EdgeSemanticWebTests(ChromeSemanticWebTests):browser_name='edge'

@unittest.skipUnless(sys.platform=='win32','Windows Brave semantic checks')
class BraveSemanticWebTests(ChromeSemanticWebTests):browser_name='brave'

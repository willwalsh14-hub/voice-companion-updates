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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class WebPolicyTests(unittest.TestCase):
    def test_http_saves_and_private_values_stay_protected(self):
        self.assertEqual(prepare_address('http://example.com:8080/Case?q=One')[0],'http://example.com:8080/Case?q=One')
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend())
            session.open('http://example.com/Case');self.assertTrue(session.navigation_succeeded)
            session.favorites.add('Old site',session.snapshot['url'])
            self.assertEqual(session.favorites.get('Old site')['url'],'http://example.com/Case')
            self.assertEqual(session.menu.history()[0]['url'],'http://example.com/Case')
            with self.assertRaises(WebError):session.favorites.add('Private','http://example.com/?access_token=private')

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

    def test_edit_quick_key_types_immediately_and_enter_uses_native_default_button(self):
        self.load('''<h1>Sign in</h1><form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1;document.querySelector('#step').innerHTML='<label>Password<input type=password></label><button>Log in</button>'"><span id=step><label>Email or mobile number<input type=email required></label><button>Next</button></span></form>''')
        self.session.keyboard('Quick:e')
        self.assertIsNotNone(self.session.keyboard_field)
        self.session.keyboard_field['value']='person@example.com'
        notices=[];self.session.progress=notices.append
        result=self.session.keyboard('Done')
        self.assertEqual(self.page.evaluate('window.sent'),1,result)
        self.assertIn('Password',self.session.reading.text)
        self.assertNotIn('Email or mobile number',self.session.reading.text)
        self.assertEqual(self.page.locator('input[type=password]').count(),1)
        self.assertEqual(notices,['Loading page.'])
        self.assertNotIn('Pressed',result)

    def test_tab_saves_without_submitting_and_button_uses_the_entered_value(self):
        self.load("""<form onsubmit="event.preventDefault();window.sentEmail=this.elements.email.value;document.body.innerHTML='<h1>Password step</h1>'"><label>Email<input name=email type=email required></label><button>Next</button></form>""")
        self.session.keyboard('Quick:e')
        self.session.keyboard_field['value']='person@example.com'
        self.session.keyboard('Tab')
        self.assertIsNone(self.page.evaluate('window.sentEmail'))
        self.assertEqual(self.page.locator('input').input_value(),'person@example.com')
        self.session.keyboard('Activate')
        self.assertEqual(self.page.evaluate('window.sentEmail'),'person@example.com')
        self.assertIn('Password step',self.session.reading.text)

    def test_private_keyboard_queue_preserves_default_submit_action(self):
        import queue
        from unittest.mock import Mock,patch
        from types import SimpleNamespace
        import companion as app
        self.load("""<form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1;document.body.innerHTML='<h1>Signed in fixture</h1>'"><label>Password<input type=password required></label><button>Log in</button></form>""")
        self.session.keyboard('Quick:e')
        window=Mock(commands=queue.Queue())
        window.commands.put(('web_private_value',{'value':'FixtureOnly','next':'Done'}))
        with patch.object(app,'APP_WINDOW',window),patch.object(app,'web_session',self.session),patch.object(app,'pending_website',None),patch.object(app,'sync_app_context'),patch.object(app,'interrupt_speech'),patch.object(app,'speak_web_result'),patch.object(app,'KEYBOARD_BATCH_TEXT',None):
            self.assertEqual(app.process_keyboard_batch('web'),'web')
        self.assertEqual(self.page.evaluate('window.sent'),1)
        self.assertIn('Signed in fixture',self.session.reading.text)
        self.assertNotIn('FixtureOnly',self.session.reading.text)

    def test_next_waits_for_async_response_after_button_state_changes(self):
        self.context.route('https://fixture.test/signin-step',lambda route:route.fulfill(content_type='application/json',body='{}'))
        self.load("""<form onsubmit="event.preventDefault();this.querySelector('button').disabled=true;fetch('https://fixture.test/signin-step').then(()=>new Promise(resolve=>setTimeout(resolve,600))).then(()=>{document.body.innerHTML='<label>Password<input type=password></label><button>Log in</button>'})"><label>Email<input type=email value='person@example.com'></label><button>Next</button></form>""")
        self.session.keyboard('Quick:b')
        self.session.keyboard('Activate')
        self.page.get_by_label('Password').wait_for(state='visible')
        self.session.refresh_changed()
        self.assertIn('Password',self.session.reading.text)
        self.assertNotIn('Email',self.session.reading.text)

    def test_rejected_next_request_is_reported_without_exposing_private_data(self):
        self.context.route('https://fixture.test/signin-rejected',lambda route:route.fulfill(status=403,body='Forbidden'))
        self.load("""<form onsubmit="event.preventDefault();fetch('https://fixture.test/signin-rejected',{method:'POST',body:'FixtureOnly'}).then(()=>{})"><label>Email<input type=email value='person@example.com'></label><button>Next</button></form>""")
        self.session.keyboard('Quick:b')
        result=self.session.keyboard('Activate')
        self.assertIn('HTTP 403',result)
        self.assertIn('has not moved',result)
        self.assertNotIn('FixtureOnly',result)
        self.assertNotIn('Page loaded',result)

    def test_noop_button_does_not_claim_a_new_page_loaded_or_click_twice(self):
        self.load('<form><label>Email<input type=email></label><button type=button onclick="window.clicks=(window.clicks||0)+1">Next</button></form>')
        self.session.keyboard('Quick:b')
        result=self.session.keyboard('Activate')
        self.assertIn('has not moved',result)
        self.assertNotIn('Page loaded',result)
        self.assertEqual(self.page.evaluate('window.clicks'),1)

    def test_enter_keeps_native_required_field_validation(self):
        self.load('<form onsubmit="event.preventDefault();window.sent=1"><label>Email<input type=email required></label><button>Continue</button></form>')
        self.session.keyboard('Quick:e')
        result=self.session.keyboard('Done')
        self.assertFalse(self.page.evaluate('window.sent'))
        self.assertIn('Email',result)

    def test_button_activation_omits_pressed_announcement(self):
        self.load('<button onclick="document.body.innerHTML=\'<h1>Course page</h1>\'">Buy Catching up with Leasey</button>')
        self.session.keyboard('Quick:b')
        notices=[];self.session.progress=notices.append
        result=self.session.keyboard('Activate')
        self.assertEqual(notices,['Loading page.'])
        self.assertNotIn('Pressed',result)
        self.assertIn('Course page',self.session.reading.text)

    def test_verification_frame_activates_once_and_refreshes_new_accessible_controls(self):
        # A provider may clear its checkbox while opening a separate challenge.
        # Companion must expose that challenge, never retry to force it checked.
        markup = """<label><input id=box type=checkbox onclick="window.clicks=(window.clicks||0)+1;this.checked=false;parent.postMessage('challenge','*')">I am not a robot</label>"""
        challenge = '<label for=answer>Accessibility answer</label><input id=answer><button>Verify answer</button>'
        self.context.route('https://verify.test/checkbox',lambda r:r.fulfill(content_type='text/html',body=markup))
        self.context.route('https://verify.test/challenge',lambda r:r.fulfill(content_type='text/html',body=challenge))
        self.load("""<p>Sign in</p><iframe title="Verification checkbox" src="https://verify.test/checkbox"></iframe><iframe id=challenge hidden title="Verification challenge" src="https://verify.test/challenge"></iframe><button>Continue sign in</button><script>addEventListener('message',e=>{if(e.origin==='https://verify.test'&&e.data==='challenge')document.querySelector('#challenge').hidden=false})</script>""")
        self.session.keyboard('Quick:x')
        self.assertEqual(self.session.browse.current()['label'],'I am not a robot')
        result=self.session.keyboard('Space')
        frame=next(f for f in self.page.frames if f.url.endswith('/checkbox'))
        self.assertEqual(frame.evaluate('window.clicks'),1)
        self.assertFalse(frame.locator('#box').is_checked())
        self.assertNotIn('could not be used',result)
        self.page.locator('#challenge').wait_for(state='visible')
        self.session._refresh(read=False,preserve=True,wait=False)
        self.assertIn('Accessibility answer',self.session.reading.text)
        self.assertIn('Verify answer',self.session.reading.text)
        self.assertIn('Accessibility answer',self.session.keyboard('Tab'))
        self.assertTrue(self.session.keyboard_field)
        self.assertIn('Verify answer',self.session.keyboard('Tab'))
        self.assertIn('Accessibility answer',self.session.keyboard('ShiftTab'))
        self.assertEqual(frame.evaluate('window.clicks'),1)

    def test_busy_checkbox_waits_without_another_activation_and_native_controls_still_work(self):
        self.load('<div role="checkbox" tabindex="0" aria-label="Human verification" aria-checked="false" aria-busy="true" onclick="window.clicks=(window.clicks||0)+1"></div><label><input type=checkbox id=ordinary>Remember me</label><label><input type=radio name=plan id=plan>Plan</label>')
        self.session.keyboard('Quick:x')
        self.assertIn('busy',self.session.keyboard('Activate'))
        self.assertIsNone(self.page.evaluate('window.clicks'))
        self.session.keyboard('Quick:x');self.session.keyboard('Space')
        self.assertTrue(self.page.locator('#ordinary').is_checked())
        self.session.keyboard('Space');self.assertFalse(self.page.locator('#ordinary').is_checked())
        self.session.keyboard('Quick:r');self.session.keyboard('Activate')
        self.assertTrue(self.page.locator('#plan').is_checked())

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

    def test_http_navigation_and_server_redirects_open_without_a_block(self):
        reached=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                reached.append(self.path)
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
                self.wfile.write(b'<title>HTTP page</title><p>HTTP content</p>')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        address='http://127.0.0.1:'+str(server.server_address[1])
        self.session.open(address+'/page');self.assertTrue(self.session.navigation_succeeded)
        self.assertIn('HTTP content',self.session.reading.text)
        self.context.route('https://fixture.test/downgrade',lambda r:r.fulfill(status=302,headers={'location':address+'/destination'}))
        self.session.open('https://fixture.test/downgrade')
        self.assertTrue(self.session.navigation_succeeded)
        self.assertEqual(self.page.url,address+'/destination')
        self.assertIn('/destination',reached)
        self.assertIn('HTTP content',self.session.reading.text)

    def test_delayed_same_document_signin_hides_old_field_and_updates_view(self):
        self.load("""<p>Sign in</p><form><section id=emailstep><label>Email<input name=email></label><button type=button onclick="setTimeout(()=>{document.querySelector('#emailstep').hidden=true;document.querySelector('#passwordstep').hidden=false},450)">Next</button></section><section id=passwordstep hidden><label>Password<input type=password></label></section></form>""")
        self.session.keyboard('Quick:e');self.session.keyboard('Activate')
        self.assertIsNotNone(self.session.keyboard_field)
        self.page.locator('button').click()
        self.page.wait_for_function("()=>document.querySelector('#emailstep').hidden")
        self.assertFalse(self.backend.field_exists(self.session.keyboard_field))
        self.session.refresh_changed()
        self.assertIsNone(self.session.keyboard_field)
        self.assertNotIn('Email, edit field',self.session.reading.text)
        self.assertIn('Password, password field',self.session.reading.text)
        self.session.keyboard('PageTop')
        for _ in range(4):
            self.session.keyboard('Read:Down')
            if self.session.keyboard_field:break
        self.assertEqual(self.session.keyboard_field['type'],'password')

    def test_arrows_enter_text_fields_and_leave_without_losing_text(self):
        self.load('<p>Before</p><label>Name<input id=name></label><p>After</p>')
        for _ in range(4):
            self.session.keyboard('Read:Down')
            if self.session.keyboard_field:break
        self.assertEqual(self.session.keyboard_field['id'],'name')
        self.session.keyboard_field['value']='Typed answer'
        self.session.keyboard('Leave:Down')
        self.assertEqual(self.page.locator('#name').input_value(),'Typed answer')
        self.assertIsNone(self.session.keyboard_field)
        self.assertIn('After',self.session.reading.move('line',0))

    def test_positive_tabindex_inside_frame_stays_with_frame_owner(self):
        self.load('<a href="/a">Before frame</a><iframe src="https://fixture.test/frame"></iframe><a href="/b" tabindex=1>First on page</a><a href="/c">After frame</a>')
        self.page.frames[1].set_content('<a href="https://fixture.test/a" tabindex=2>Frame second</a><a href="https://fixture.test/b" tabindex=1>Frame first</a>')
        self.session._refresh(wait=False)
        self.assertEqual([e['label'] for e in self.session.link_items()],['First on page','Before frame','Frame first','Frame second','After frame'])

    def test_named_lists_use_plain_names_and_native_tabindex_order(self):
        self.load('<a href="/a">Ordinary</a><a href="/b" tabindex=2>Second</a><a href="/c" tabindex=1>First</a><h2>Details</h2><h3>More</h3>')
        self.assertEqual([i['label'] for i in self.session.link_items()],['First','Second','Ordinary'])
        self.session.keyboard('Tab');self.assertEqual(self.session.browse.current()['label'],'First')
        self.session.keyboard('Tab');self.assertEqual(self.session.browse.current()['label'],'Second')
        self.assertNotIn('Link First',self.session.list_links())
        self.assertNotIn('Link Second',self.session.move_link(1))
        self.assertIn('Details 2',self.session.list_headings())
        self.assertNotIn('heading level',self.session.move_heading(1))
        self.assertIn('More 3',self.session.list_name_input('M',now=100))

    def test_arrow_reading_names_native_image_buttons_and_form_controls(self):
        self.load('<p>Before controls</p><form><input type="image" alt="Use PayPal or credit or debit card to buy Course" src="button.png"><label>Email<input name="email" required></label><label>Password<input type="password" value="never-speak-this"></label><label><input type="checkbox" checked>Remember me</label><select aria-label="Country"><option>UK</option></select></form>')
        text=self.session.reading.text
        self.assertIn('Course, button',text)
        self.assertIn('Email, edit field, required',text)
        self.assertIn('Password, password field',text)
        self.assertIn('Remember me, checkbox, checked',text)
        self.assertIn('Country, combo box',text)
        self.assertNotIn('never-speak-this',text)
        button=next(e for e in self.session.snapshot['elements'] if kind(e)=='button')
        self.session.reading.position=button['start']-1
        self.assertIn('button',self.session.keyboard('Read:Down'))
        self.assertEqual(self.session.browse.current()['key'],button['key'])

    def test_arrow_from_long_paragraph_targets_short_next_button(self):
        self.load('<p>A long paragraph before the short button.</p><button onclick="document.querySelector(\'#result\').textContent=\'Next step\'">Next</button><p id=result>Not yet</p>')
        self.session.reading.position=25
        self.assertIn('Next, button',self.session.keyboard('Read:Down'))
        self.assertEqual(kind(self.session.browse.current()),'button')
        self.session.keyboard('Activate')
        self.assertEqual(self.page.locator('#result').inner_text(),'Next step')

    def test_image_submit_opens_target_once_and_reports_loading(self):
        self.context.route('https://fixture.test/checkout',lambda r:r.fulfill(content_type='text/html',body='<h1>Checkout page</h1>'))
        self.load('<p>Course</p><form action="https://fixture.test/checkout" method="post"><input type="image" alt="Buy course" src="button.png"></form>')
        notices=[];self.session.progress=notices.append
        self.session.keyboard('Quick:b')
        result=self.session.keyboard('Activate')
        self.assertEqual(notices,['Loading page.'])
        self.assertEqual(self.page.url,'https://fixture.test/checkout')
        self.assertIn('Checkout page',self.session.reading.text)
        self.assertNotIn('review',result.lower())

    def test_next_submit_runs_native_validation_and_same_page_signin_step(self):
        self.load('<p>Sign in</p><form onsubmit="event.preventDefault(); document.querySelector(\'#step\').innerHTML=\'<label>Password<input type=password></label><button>Log in</button>\'"><label>Email<input type=email required></label><span id=step><button>Next</button></span></form>')
        self.session.keyboard('Quick:b');self.session.keyboard('Activate')
        self.assertEqual(self.page.locator('input[type=password]').count(),0)
        self.session.keyboard('Quick:e');self.session.keyboard('Activate')
        self.session.keyboard_field['value']='person@example.com';self.session.keyboard('Tab')
        # The current native submitter is activated through voice and keyboard alike.
        result=self.session.command('activate current button')
        self.assertEqual(self.page.locator('input[type=password]').count(),1,result)
        self.assertIn('password field',self.session.reading.text)
        self.assertIsNone(self.session.submit_pending)


@unittest.skipUnless(sys.platform=='win32','Windows Chrome semantic checks')
class ChromeSemanticWebTests(SemanticWebTests):
    browser_name='chrome'
    setUpClass=classmethod(consumer_helpers.ChromeConsumerWebTests.setUpClass.__func__)

@unittest.skipUnless(sys.platform=='win32','Windows Edge semantic checks')
class EdgeSemanticWebTests(ChromeSemanticWebTests):browser_name='edge'

@unittest.skipUnless(sys.platform=='win32','Windows Brave semantic checks')
class BraveSemanticWebTests(ChromeSemanticWebTests):browser_name='brave'

import tempfile
import unittest

from web_assistant import BrowserBackend, Favorites, WebSession, match_choices


class FakeBackend:
    def __init__(self):
        self.url = 'https://example.com/'
        self.activated = []
        self.filled = []
        self.controls = [
            {'key':'0','role':'link','label':'Log in','href':'https://example.com/login','type':'','tag':'a'},
            {'key':'1','role':'link','label':'Transfer between accounts','href':'https://example.com/transfer','type':'','tag':'a'},
            {'key':'2','role':'field','label':'Full name','href':'','type':'text','tag':'input','value':''},
            {'key':'3','role':'field','label':'Password','href':'','type':'password','tag':'input'},
            {'key':'4','role':'button','label':'Submit application','href':'','type':'submit','tag':'button'},
            {'key':'5','role':'button','label':'Continue','href':'','type':'submit','tag':'button','in_form':True},
        ]
    def goto(self, url): self.url = url
    def back(self): self.url = 'https://example.com/'
    def snapshot(self):
        return {'title':'Example account', 'url':self.url,
                'text':'Welcome to the account. Log in or transfer between accounts. Fill out an application.',
                'controls':self.controls}
    def activate(self, item): self.activated.append(item['key'])
    def fill(self, item, value):
        self.filled.append((item['key'], value))
        next(c for c in self.controls if c['key'] == item['key'])['value'] = value
    def choose_form_option(self, item, answer):
        control = next(c for c in self.controls if c['key'] == item['key'])
        if item['tag'] == 'select': control['selected_label'] = answer
        else: control['checked'] = answer == 'yes'


class WebTests(unittest.TestCase):
    def test_waits_for_late_page_text_before_reporting_empty(self):
        original = self.backend.snapshot
        calls = [0]
        self.backend.settle = lambda: None
        def delayed():
            calls[0] += 1
            result = original()
            if calls[0] < 4:
                result['text'] = ''
                result['controls'] = []
            return result
        self.backend.snapshot = delayed
        self.assertIn('Welcome to the account', self.session.open('example.com'))
        self.assertEqual(calls[0], 4)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = FakeBackend()
        self.session = WebSession(self.temp.name, self.backend)

    def test_guided_login_and_page_reading(self):
        self.assertIn('Page: Example account', self.session.open('example.com'))
        self.assertIn('Log in', self.session.command("Um, I'm looking for how to log in"))
        self.assertEqual(self.backend.activated, [])
        self.assertIn('Is that what you want?', self.session.command('I am looking for login'))
        self.session.command('cancel')
        self.assertEqual(self.backend.activated, [])
        self.session.command('I am looking for login')
        self.assertIn('Page: Example account', self.session.command('yes'))
        self.assertEqual(self.backend.activated, ['0'])
        self.assertIn('Welcome', self.session.command('read page'))

    def test_no_offers_next_match_and_named_choice(self):
        self.session.open('example.com')
        self.assertIn('Tell me what you want', self.session.command('help me'))
        self.assertIn('Log in', self.session.command('I am looking for login or transfer'))
        self.assertIn('Transfer between accounts', self.session.command('no'))
        self.assertIn('Page: Example account', self.session.command('yes'))
        self.assertEqual(self.backend.activated, ['1'])

    def test_transfer_link_and_submission_guard(self):
        self.session.open('example.com')
        self.assertIn('Transfer between accounts', self.session.command('I want to transfer money between my accounts'))
        self.session.command('open choice 1')
        self.session.command('confirm web action')
        self.assertEqual(self.backend.activated, ['1'])
        self.assertIn('Submit application', self.session.command('find submit application'))
        self.assertIn('Ask your helper', self.session.command('yes'))
        self.assertEqual(self.backend.activated, ['1'])
        self.session.command('find continue')
        self.assertIn('Ask your helper', self.session.command('yes'))
        self.assertEqual(self.backend.activated, ['1'])

    def test_favorites_and_form_fields(self):
        self.session.open('example.com')
        self.assertIn('Saved favorite Bank', self.session.command('save favorite as Bank'))
        self.assertIn('Bank', self.session.command('list favorites'))
        self.assertIn('Page:', self.session.command('that one'))
        self.session.command('save favorite as Portal')
        self.assertIn('Bank', self.session.command('list favorites'))
        self.assertIn('Portal', self.session.command('next'))
        self.assertIn('Page:', self.session.command('confirm'))
        self.assertEqual(Favorites(self.temp.name).get('bank')['url'], 'https://example.com')
        self.assertIn('Page: Example account', self.session.command('open favorite Bank'))
        self.assertIn('Full name', self.session.command('list fields'))
        self.assertNotIn('Field 2:', self.session.command('list fields'))
        self.assertIn('Password', self.session.command('next'))
        self.assertIn('Full name', self.session.command('previous'))
        self.assertIn('Filled Full name', self.session.command('fill field 1 with David McKeehan'))
        self.assertEqual(self.backend.filled, [('2', 'David McKeehan')])
        self.assertIn('private entry', self.session.command('fill field 2 with secret'))
        self.assertEqual(len(self.backend.filled), 1)
        self.assertIn('What should I enter?', self.session.command('fill out this form for me'))
        self.assertIn('Full name', self.session.command('help me'))
        answer = self.session.command('David McKeehan')
        self.assertIn('Recorded David McKeehan for Full name', answer)
        self.assertIn('Review every answer', answer)
        self.assertEqual(len(self.backend.filled), 2)
        self.assertIn('Password', answer)
        self.assertIn('Full name: David McKeehan', self.session.command('review form'))
        handoff = self.session.command('ask for help')
        self.assertIn('example.com', handoff)
        self.assertNotIn('David McKeehan', handoff)

    def test_search_and_matching_reject_empty(self):
        self.assertEqual(match_choices(self.backend.controls, 'um uh'), [])
        self.assertIn('Page: Example', self.session.command('look up public benefits'))
        self.assertIn('search.brave.com/search?q=public%20benefits', self.backend.url)
        self.assertIn('Page: Example', self.session.command('what about eligibility'))
        self.assertIn('search.brave.com/search?q=public%20benefits%20eligibility', self.backend.url)
        self.assertIn('Source: Example account', self.session.command('what does this page say about transfer'))

    def test_numbered_links_and_late_search_results(self):
        self.session.open('example.com')
        self.assertIn('Log in (L), 1 of 2', self.session.command('list links'))
        self.assertIn('Open link Log in', self.session.command('open link 1'))
        self.assertIn('Page: Example account', self.session.command('yes'))
        self.assertEqual(self.backend.activated, ['0'])
        self.backend.controls = []
        original = self.backend.snapshot
        calls = [0]
        def delayed_snapshot():
            calls[0] += 1
            if calls[0] > 1: self.backend.controls = [
                {'key':'7','role':'link','label':'Veterans benefits','href':'https://example.com/benefits','tag':'a'}]
            return original()
        self.backend.snapshot = delayed_snapshot
        self.session.open('example.com')
        self.assertIn('Veterans benefits', self.session.command('find veterans benefits'))

    def test_search_challenge_is_spoken_and_falls_back(self):
        original = self.backend.snapshot
        def challenge_then_results():
            snap = original()
            if 'google.com' in self.backend.url:
                snap.update(title='Human verification', text='', controls=[], challenge=True)
            return snap
        self.backend.snapshot = challenge_then_results
        result = self.session.command('google veterans benefits')
        self.assertIn('Google asked for human verification', result)
        self.assertIn('Brave Search', result)
        self.assertIn('search.brave.com', self.backend.url)
        self.backend.url = 'https://www.google.com/sorry/index'
        self.assertIn('human verification', self.session._refresh())

    def test_search_all_engines_blocked_gives_honest_speech(self):
        original = self.backend.snapshot
        def blocked():
            snap = original()
            snap.update(text='', controls=[], challenge=True)
            return snap
        self.backend.snapshot = blocked
        result = self.session.search('help with email')
        self.assertIn('human verification', result)
        self.assertNotIn('Page:', result)

    def test_browser_snapshot_includes_embedded_page_links(self):
        class Frame:
            def __init__(self, url, label):
                self.url, self.label = url, label
            def evaluate(self, script):
                self.script = script
                return {'title':'Page', 'url':self.url, 'text':'Content',
                        'controls':[{'key':'0', 'role':'link', 'label':self.label,
                                     'href':self.url+'/login', 'tag':'a'}]}
        main = Frame('https://example.com', 'Top link')
        child = Frame('https://accounts.example.com', 'Sign in')
        main.main_frame = main
        main.frames = [main, child]
        backend = BrowserBackend(self.temp.name)
        backend.page = main
        backend.start = lambda: None
        result = backend.snapshot()
        self.assertEqual([c['label'] for c in result['controls']], ['Top link', 'Sign in'])
        self.assertEqual(result['controls'][1]['frame_index'], 1)
        self.assertIn('shadowRoot', main.script)

    def test_mixed_web_speech_types_into_focused_field(self):
        self.session.open('example.com')
        self.backend.dictate_focused = lambda spoken: 'Added text to Search. Review the field before submitting.'
        self.assertIn('Added text to Search', self.session.command('veterans benefits near me'))
        self.session.input_mode = 'commands'
        self.assertIn('did not identify', self.session.command('veterans benefits near me')
                      .replace('could not identify', 'did not identify'))

    def test_web_reading_units(self):
        self.session.open('example.com')
        self.assertIn('Welcome', self.session.command('start reading'))
        self.assertIn('Character', self.session.command('next character'))
        self.assertIn('Word', self.session.command('next word'))
        self.assertIn('Sentence', self.session.command('next sentence'))

    def test_browser_dictation_respects_focused_field_and_password(self):
        class Frame:
            def __init__(self, field): self.field = field
            def evaluate(self, script): return self.field
        class Keyboard:
            inserted = []
            def insert_text(self, value): self.inserted.append(value)
        backend = BrowserBackend(self.temp.name)
        frame = Frame({'tag':'input','type':'password','editable':False,'label':'Password'})
        backend.page = type('Page', (), {'frames':[frame], 'keyboard':Keyboard()})()
        backend.start = lambda: None
        self.assertIn('private', backend.dictate_focused('secret'))
        self.assertEqual(backend.page.keyboard.inserted, [])
        frame.field = {'tag':'textarea','type':'','editable':False,'label':'Comments'}
        self.assertIn('Added text to Comments', backend.dictate_focused('Hello there'))
        self.assertEqual(backend.page.keyboard.inserted, ['Hello there'])

    def test_complete_text_choice_form_and_submit_after_review(self):
        self.backend.controls = [
            {'key':'1','role':'field','label':'Full name','tag':'input','type':'text','value':'','required':True},
            {'key':'2','role':'field','label':'Agree to terms','tag':'input','type':'checkbox','checked':False},
            {'key':'3','role':'field','label':'State','tag':'select','type':'select-one','selected_label':'Choose',
             'options':[{'label':'Choose','value':''},{'label':'Oregon','value':'OR'}]},
            {'key':'4','role':'button','label':'Submit application','tag':'button','type':'submit','in_form':True},
        ]
        self.session.open('example.com')
        self.assertIn('Full name', self.session.command('fill out this form for me'))
        self.assertIn('Agree to terms', self.session.command('David McKeehan'))
        self.assertIn('Choices:', self.session.command('yes'))
        self.assertIn('Review every answer', self.session.command('Oregon'))
        review = self.session.command('review and submit form')
        self.assertIn('Full name: David McKeehan', review)
        self.assertIn('Agree to terms: checked', self.session.command('continue review'))
        self.assertIn('State: Oregon', self.session.command('continue review'))
        self.assertEqual(self.backend.activated, [])
        self.assertIn('I pressed Submit application', self.session.command('submit this form'))
        self.assertEqual(self.backend.activated, ['4'])
        self.assertIn('no reviewed form', self.session.command('submit this form'))

    def test_browser_choice_and_keyboard_edit_appear_in_review(self):
        self.session.open('example.com')
        self.assertIn('Chrome', self.session.command('use Chrome'))
        self.assertIn('Chrome', self.session.command('which browser'))
        self.backend.controls = [
            {'key':'1','role':'field','label':'Name','tag':'input','type':'text','value':'typed by keyboard'},
            {'key':'2','role':'button','label':'Submit application','tag':'button','type':'submit','in_form':True},
        ]
        self.session.open('example.com')
        self.assertIn('Name: typed by keyboard', self.session.command('review and submit form'))
        self.backend.controls[0]['value'] = 'edited by keyboard'
        self.assertIn('answer changed', self.session.command('submit this form'))
        self.assertEqual(self.backend.activated, [])

    def test_form_change_cancels_submission_and_financial_action_needs_review(self):
        self.backend.controls = [
            {'key':'1','role':'field','label':'Amount','tag':'input','type':'text','value':''},
            {'key':'2','role':'button','label':'Transfer money','tag':'button','type':'submit','in_form':True},
        ]
        self.session.open('example.com')
        self.session.command('fill out this form')
        self.session.command('100')
        self.assertIn('move money', self.session.command('review and submit form'))
        self.assertEqual(self.backend.activated, [])
        self.backend.controls[1]['label'] = 'Submit application'
        self.assertIn('Is every field right?', self.session.command('review and submit form'))
        self.backend.controls[0]['value'] = '1000'
        self.assertIn('answer changed', self.session.command('submit this form'))
        self.assertEqual(self.backend.activated, [])


if __name__ == '__main__': unittest.main()

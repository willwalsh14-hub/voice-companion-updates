"""A customer's day with the web browser, radio and podcasts, through the real app.

Each test follows what a new user or a screen reader user really says,
including recognizer punctuation, and checks every word they hear."""
import io,tempfile,unittest
from contextlib import ExitStack,redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock,patch
import companion as app
from field_selection import FieldSelection
from reading_navigation import ReadingCursor


class Journey(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        defaults={'APP':self.root,'TEXT_MODE':True,'APP_WINDOW':None,'INPUT_MODE':'mixed','document':None,'email_draft':None,'mail_session':None,'web_session':None,'pending_website':None,'pending_send':None,'NAMED_LIST':None,'account_setup':False,'MAIN_MENU_INDEX':None,'MAIN_MENU_LEAD':'','SYNTH_PICK_INDEX':None,'VOICE_PICK_INDEX':None,'VOICE_PICK_ORIGINAL':None,'help_session':None,'tutorial_session':None,'MEDIA_HUB':None,'MEDIA_NAV':None,'MEDIA_SECTION':'radio','note_buffer':[],'note_selection':FieldSelection(),'note_reading':ReadingCursor(),'KEYBOARD_DIRTY':{},'EDIT_ACK':{},'PREFERENCES':dict(app.PREFERENCES),'DOCUMENT_BASELINE':None,'SLEEP_RETURN_MODE':'awake','SEARCH_RETURN_MODE':'awake'}
        for name,value in defaults.items():self.stack.enter_context(patch.object(app,name,value))
        self.spoken=[]
        self.stack.enter_context(patch.object(app,'speak',side_effect=lambda text,*a,**k:self.spoken.append(str(text))))

    def say(self,text,mode,typed=False):
        self.spoken.clear()
        return app.handle(text,mode,typed)


class WebsiteByVoiceTests(Journey):
    def web(self):
        session=Mock(snapshot=None,page_revision=0,pending=None,submit_pending=None,list_focus=None,keyboard_field=None)
        def opened(url):
            session.snapshot={'url':url,'title':'VA.gov','text':'Welcome'};session.page_revision+=1
            return 'Page loaded: VA.gov.'
        session.open.side_effect=opened
        session.search.side_effect=lambda query:'Searching for '+query+'.'
        self.stack.enter_context(patch.object(app,'web_session',session))
        return session

    def test_yes_opens_the_website_asked_for_from_the_main_menu(self):
        """"Yes" was taken as choosing a main menu item, so the site never opened."""
        for answer in ('Yes, please.','Yes.','yes','Okay.'):
            with self.subTest(answer=answer):
                session=self.web()
                self.assertEqual(self.say('Open website VA dot gov.','awake'),'awake')
                self.assertEqual(self.spoken,['I heard website va.gov. Is that right? Say yes or no.'])
                self.assertEqual(self.say(answer,'awake'),'web')
                session.open.assert_called_once_with('https://va.gov')
                self.assertNotIn('Say next or previous to choose an option.',self.spoken)

    def test_no_cancels_and_the_main_menu_still_answers_yes_afterwards(self):
        session=self.web()
        self.say('open website VA dot gov','awake');self.say('No, thanks.','awake')
        self.assertEqual(self.spoken,['Website request canceled. Say open website followed by the address to try again.'])
        session.open.assert_not_called()
        self.say('yes','awake');self.assertEqual(self.spoken,['Say next or previous to choose an option.'])

    def test_open_website_works_on_the_web_browser_menu_and_on_a_page(self):
        """On the web browser menu, "Open website" said "Choose a web browser menu option."."""
        for mode in ('browser_menu','web'):
            with self.subTest(mode=mode):
                session=self.web()
                if mode=='web':session.snapshot={'url':'https://example.com/','title':'Example','text':'Example'}
                session.menu.level='menu'
                self.assertEqual(self.say('Open website VA dot gov.',mode),mode)
                self.assertEqual(self.spoken,['I heard website va.gov. Is that right? Say yes or no.'])
                self.assertEqual(self.say('Yes, please.',mode),'web')
                session.open.assert_called_once_with('https://va.gov')


class MainMenuSaidOnceTests(Journey):
    """The main menu prompt was said twice, or a closing message was cut off by it."""
    PROMPT=app.MAIN_MENU_PROMPT

    def said_main_menu(self):
        return [text for text in self.spoken if 'Main menu' in text]

    def test_main_menu_from_a_web_page_is_said_once(self):
        session=Mock(snapshot={'url':'https://va.gov/','title':'VA','text':'Welcome'},page_revision=0,pending=None,submit_pending=None,list_focus=None,keyboard_field=None)
        with patch.object(app,'web_session',session):
            self.assertEqual(self.say('Main menu.','web'),'awake')
        self.assertEqual(self.spoken,[self.PROMPT])

    def test_escape_on_the_web_browser_menu_says_main_menu_once(self):
        from browser_menu import BrowserMenu
        session=Mock(snapshot=None,page_revision=0,pending=None,submit_pending=None,list_focus=None,keyboard_field=None)
        session.menu=BrowserMenu(self.root)
        with patch.object(app,'web_session',session):
            session.menu.start()
            self.assertEqual(self.say('escape','browser_menu',typed=True),'awake')
        self.assertEqual(self.spoken,[self.PROMPT])

    def test_closing_the_browser_is_said_with_the_main_menu_in_one_message(self):
        session=Mock(snapshot={'url':'https://va.gov/','title':'VA','text':'Welcome'},page_revision=0,pending=None,submit_pending=None,list_focus=None,keyboard_field=None)
        session.backend.regular=False
        with patch.object(app,'web_session',session):
            self.assertEqual(self.say('Close browser window.','web'),'awake')
        self.assertEqual(self.spoken,['Browser window closed. '+self.PROMPT])


class Player:
    def __init__(self):self.kind=None;self.calls=[]
    def open(self,url,title,kind):self.calls.append(('open',url,title,kind));self.kind=kind
    def control(self,action,seconds=15,position=None):self.calls.append((action,seconds,position));return 0
    def close(self):self.kind=None


class RadioJourneyTests(Journey):
    def hub(self,stations=None,shows=None):
        from media_hub import MediaHub
        hub=MediaHub(self.root,player=Player(),clock=lambda:datetime(2026,10,12,15,0))
        catalog=stations if stations is not None else [{'name':'WGN Radio 720','url':'https://audio.test/one'}]
        podcasts=[{'collectionName':name,'feedUrl':'https://feeds.test/'+str(i)} for i,name in enumerate(shows or ['Planet Money'])]
        hub.fetch_json=lambda url:{'results':podcasts} if 'itunes' in url else catalog
        hub.choose_source('radio browser');hub.start_scheduler=lambda:None
        self.stack.enter_context(patch.object(app,'MEDIA_HUB',hub))
        return hub

    def test_one_result_is_counted_in_plain_words(self):
        """"Found 1 stations." and "Found 1 podcasts." were said."""
        hub=self.hub()
        self.assertTrue(hub.search_radio('name','WGN').startswith('Found 1 station. Station 1: WGN Radio 720.'))
        self.assertTrue(hub.search_podcasts('Planet').startswith('Found 1 podcast. Podcast 1: Planet Money.'))
        hub=self.hub(stations=[{'name':'WGN One','url':'https://audio.test/1'},{'name':'WGN Two','url':'https://audio.test/2'}],shows=['One','Two'])
        self.assertTrue(hub.search_radio('name','WGN').startswith('Found 2 stations.'))
        self.assertTrue(hub.search_podcasts('Show').startswith('Found 2 podcasts.'))

    def test_scheduled_recordings_are_said_as_people_say_dates(self):
        """The list read "2026-10-13T20:00:00", and the confirmation said "08:00 PM"."""
        self.hub()
        mode=self.say('radio','awake');self.say('search WGN',mode)
        self.say('Schedule station 1 tomorrow at 8 PM for 30 minutes.',mode)
        self.assertEqual(self.spoken,['Scheduled WGN Radio 720 for Tuesday, October 13 at eight PM for 30 minutes. Keep Voice Companion running and the computer awake.'])
        self.say('schedule station 1 tomorrow at 7:05 am for 15 minutes every day',mode)
        self.assertIn('Tuesday, October 13 at seven oh five AM for 15 minutes, every day.',self.spoken[0])
        self.say('List scheduled recordings.',mode)
        self.assertEqual(self.spoken,['Scheduled: 1. WGN Radio 720, Tuesday, October 13 at eight PM. 2. WGN Radio 720, Tuesday, October 13 at seven oh five AM, every day.'])
        self.assertNotRegex(self.spoken[0],r'\d{4}-\d\d')

    def test_leaving_radio_says_the_schedule_note_only_when_something_is_scheduled(self):
        """"Scheduled recordings continue while the app runs." was said every time, then cut off by the main menu."""
        self.hub()
        mode=self.say('radio','awake')
        self.assertEqual(self.say('Main menu.',mode),'awake')
        self.assertEqual(self.spoken,[app.MAIN_MENU_PROMPT])
        mode=self.say('radio','awake');self.say('search WGN',mode);self.say('schedule station 1 tomorrow at 8 pm for 30 minutes',mode)
        self.assertEqual(self.say('main menu',mode),'awake')
        self.assertEqual(self.spoken,['Scheduled recordings continue while the app runs. '+app.MAIN_MENU_PROMPT])


    def test_escape_from_podcasts_or_the_player_returns_to_that_media_menu_item(self):
        """Escape from Podcasts or Media player said "Media menu. Radio, 1 of 3." and lost your place."""
        self.hub()
        mode=self.say('media menu','awake');self.say('next',mode)
        mode=self.say('that one',mode);self.assertEqual(mode,'media')
        mode=app.handle_keyboard(app.navigation_key('Escape',mode),mode)
        self.assertEqual(mode,'media_menu');self.assertEqual(self.spoken[-1],'Media menu. Podcasts (P), 2 of 3.')
        self.say('next',mode);mode=self.say('that one',mode);self.assertEqual(mode,'local_media')
        self.spoken.clear();mode=app.handle_keyboard(app.navigation_key('Escape',mode),mode)
        self.assertEqual(mode,'media_menu');self.assertEqual(self.spoken[-1],'Media menu. Media player (M), 3 of 3.')
        self.say('previous',mode);self.say('previous',mode);mode=self.say('that one',mode)
        self.spoken.clear();mode=app.handle_keyboard(app.navigation_key('Escape',mode),mode)
        self.assertEqual(self.spoken[-1],'Media menu. Radio (R), 1 of 3.')


class SpokenAddressTests(Journey):
    web=WebsiteByVoiceTests.web

    def test_spoken_addresses_tolerate_underscore_and_please(self):
        from browser_navigation import prepare_address
        self.assertEqual(prepare_address('example dot com forward slash about underscore us')[0],'https://example.com/about_us')
        self.assertEqual(prepare_address('Amazon dot com, please.')[0],'https://amazon.com')
        self.assertEqual(prepare_address('please VA dot gov')[0],'https://va.gov')

    def test_an_address_said_in_the_search_box_opens_the_site(self):
        """"VA dot gov." said in Search or enter address searched Google for "VA dot gov."."""
        for spoken in ('VA dot gov.','Open website VA dot gov.','go to VA dot gov','www dot VA dot gov'):
            with self.subTest(spoken=spoken):
                session=self.web()
                self.assertEqual(self.say(spoken,'search'),'web')
                session.search.assert_not_called()
                self.assertIn(session.open.call_args.args[0],('https://va.gov','https://www.va.gov'))
        for spoken in ('weather in Houston','what is a dot com company','VA benefits'):
            with self.subTest(spoken=spoken):
                session=self.web()
                self.say(spoken,'search')
                session.open.assert_not_called();session.search.assert_called_once_with(spoken)

    def test_typed_search_box_text_stays_literal(self):
        session=self.web()
        self.say('go to VA dot gov','search',typed=True)
        session.search.assert_called_once_with('go to VA dot gov')

    def test_one_saved_favorite_is_counted_in_plain_words(self):
        from test_web_assistant import FakeBackend
        from web_assistant import WebSession
        session=WebSession(self.root,FakeBackend());session.open('https://example.com/')
        session.command('save favorite as My bank')
        self.assertTrue(session.command('list favorites').startswith('Found 1 favorite. '))


class PageWordsTests(unittest.TestCase):
    def test_no_next_item_uses_the_names_in_the_guide(self):
        """"No next edit.", "No next combobox." and "No next graphic." used code words."""
        from web_browse import BrowseCursor
        cursor=BrowseCursor({'elements':[{'role':'text','label':'Only text','start':0,'end':9}],'text':'Only text'})
        expected={'e':'text box','c':'choice box','g':'picture','f':'form control','r':'radio button','i':'list item','s':'list box','x':'checkbox','k':'link','h':'heading','d':'landmark','m':'frame'}
        for key,name in expected.items():
            with self.subTest(key=key):
                self.assertEqual(cursor.move(key),'End of list. No next '+name+'.')
                self.assertEqual(cursor.move(key,backward=True),'Start of list. No previous '+name+'.')


class LivePageTests(unittest.TestCase):
    """In a real Chromium page, as a customer uses it."""
    @classmethod
    def setUpClass(cls):
        import test_web_live
        test_web_live.LiveWebTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):
        import test_web_live
        test_web_live.LiveWebTests.tearDownClass.__func__(cls)
    def setUp(self):
        import test_web_live
        test_web_live.LiveWebTests.setUp(self)
        self.page.set_content('<title>VA.gov Home</title><h1>VA.gov Home</h1><p>Welcome.</p><label for="loc">City</label><input id="loc"><h2>News</h2><button>Sign in</button>')
        self.session._refresh()

    def test_where_am_i_names_the_page_and_the_place_on_it(self):
        """In page view, "Where am I?" said "No readable field has keyboard focus." wherever you were."""
        self.session.command('next button')
        self.assertEqual(self.session.command('Where am I?'),'Page: VA.gov Home. Sign in, button.')

    def test_type_away_from_a_text_box_says_what_to_do(self):
        """"Type hello" on a button searched the page for "type hello"."""
        self.session.command('next button')
        self.assertEqual(self.session.command('type hello'),'Move to a text box first. Say next text box, then type followed by your words.')
        self.assertEqual(self.page.locator('#loc').input_value(),'')
        self.session.keyboard('PageTop');self.session.command('next text field')
        self.assertIn('Text entered',self.session.command('type Houston'))
        self.session.keyboard('Done')
        self.assertEqual(self.page.locator('#loc').input_value(),'Houston')

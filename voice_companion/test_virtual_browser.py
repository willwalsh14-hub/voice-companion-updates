import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from web_assistant import WebSession
from web_browse import BrowseCursor
from settings_model import DEFAULTS,SettingsSession,fields
from test_web_assistant import FakeBackend
import companion as app

class VirtualBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.backend=FakeBackend();self.session=WebSession(self.temp.name,self.backend)
        self.text='Page title\nBefore the result\nFirst result title\nAfter first result\nSecond result title\nAfter second result'
        self.elements=[]
        for i,title in enumerate(('Page title','First result title','Second result title')):
            start=self.text.index(title);self.elements.append(dict(key='h'+str(i),role='heading',label=title,start=start,end=start+len(title),level=2,tag='h2'))
            if i:self.elements.append(dict(key='a'+str(i),role='link',label=title,start=start,end=start+len(title),tag='a',href='https://example.com/'+str(i)))
        self.session.snapshot={'title':'Fixture','url':'https://example.com','text':self.text,'elements':self.elements,'controls':[]}
        self.session.reading.set_text(self.text);self.session.browse=BrowseCursor(self.session.snapshot)
    def test_quick_keys_and_arrow_reading_use_one_position(self):
        self.session.keyboard('Quick:h');self.session.keyboard('Quick:h')
        self.assertEqual(self.session.reading.position,self.text.index('First result title'))
        self.assertIn('After first result',self.session.keyboard('Read:Down'))
        self.assertIn('First result title',self.session.keyboard('Read:Up'))
        self.assertIn('Second result title',self.session.keyboard('Quick:h'))
        self.assertIn('First result title',self.session.keyboard('Quick:Previous:h'))
        self.session.keyboard('Quick:k');self.session.keyboard('Quick:k');self.assertIn('After second result',self.session.keyboard('Read:Down'))
    def test_character_word_line_and_page_keys_are_shared_with_voice(self):
        self.session.keyboard('Quick:h');self.session.keyboard('Quick:h')
        self.session.keyboard('Read:Right');self.assertEqual(self.session.reading.position,self.text.index('First result title')+1)
        self.session.keyboard('Read:Control:Left');self.assertEqual(self.session.reading.position,self.text.index('First result title'))
        self.assertEqual(self.session.keyboard('Read:Control:Right'),'result')
        self.session.keyboard('Read:End');self.assertEqual(self.session.reading.position,self.text.index('First result title')+len('First result title'))
        self.session.keyboard('Read:Home');self.assertEqual(self.session.reading.position,self.text.index('First result title'))
        self.assertIn('After first result',self.session.command('next line'))
        self.assertIn('First result title',self.session.keyboard('Read:Up'))
        self.session.keyboard('PageBottom');self.assertEqual(self.session.reading.position,len(self.text))
        self.session.command('top of page');self.assertEqual(self.session.reading.position,0)
    def test_stale_caret_ack_cannot_undo_quick_navigation_and_selection_survives(self):
        with patch.object(app,'web_session',self.session),patch.object(app,'EDIT_ACK',{}):
            context=app.app_context('web');payload=dict(source=context['source'],readonly=True,after=self.text,before=self.text,caret=0,selection=None,seq=1,web_cursor_revision=context['web_cursor_revision'])
            self.session.keyboard('Quick:h');self.session.keyboard('Quick:h');position=self.session.reading.position
            self.assertFalse(app.apply_keyboard_edit(payload,'web'));self.assertEqual(self.session.reading.position,position)
            payload.update(caret=position+5,selection=(position,position+5),web_cursor_revision=self.session.cursor_revision)
            self.assertTrue(app.apply_keyboard_edit(payload,'web'));self.assertEqual(app.app_context('web')['selection'],(position,position+5))
            self.session.keyboard('Quick:h');self.assertIsNone(app.app_context('web')['selection'])
    def test_continue_reading_keeps_its_continuation(self):
        text='Reading text. '*250;self.session.snapshot.update(text=text,elements=[]);self.session.reading.set_text(text,reset=True);self.session.browse=BrowseCursor(self.session.snapshot)
        self.session.command('read from beginning');first=self.session.reading.continuation
        self.session.command('continue reading');self.assertGreater(self.session.reading.continuation,first)
    def test_google_default_and_retained_search_engines(self):
        self.assertEqual(DEFAULTS['web_search_engine'],'google')
        for engine,host in [('google','www.google.com'),('bing','www.bing.com'),('brave','search.brave.com'),('duckduckgo','html.duckduckgo.com')]:
            self.session.search_engine=engine;self.session.search('same words');self.assertIn(host,self.backend.url)
        choices=next(f.choices for f in fields('Web browser',{}) if f.key=='web_search_engine');self.assertEqual(set(choices),{'google','bing','brave','duckduckgo'})
        settings=SettingsSession({},{});settings.set('web_search_engine','brave');self.assertEqual(settings.values['web_search_engine'],'brave')
        for phrase,engine in [('default search engine google','google'),('set default search engine Brave Search','brave'),('search engine DuckDuckGo','duckduckgo')]:
            with patch.object(app,'web_session',self.session),patch.object(app,'PREFERENCES',{}),patch.object(app,'save_preferences'),patch.object(app,'speak'):
                self.assertEqual(app.handle(phrase,'awake'),'awake');self.assertEqual(app.PREFERENCES['web_search_engine'],engine)
    def test_menu_labels_and_voice_aliases_keep_existing_workflows(self):
        self.assertIn(('Word processor','create a document'),app.MAIN_MENU_CHOICES);self.assertIn(('Notepad','write a note'),app.MAIN_MENU_CHOICES)
        with patch.object(app,'documents_folder',return_value=Path(self.temp.name)),patch.object(app,'speak'),patch.object(app,'APP_WINDOW',None),patch.object(app,'document',None),patch.object(app,'TEXT_MODE',True):
            self.assertEqual(app.handle('word processor','awake'),'document');self.assertEqual(app.handle('notepad','awake'),'note')

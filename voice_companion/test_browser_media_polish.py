import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from datetime import datetime
from media_hub import MediaHub
from media_navigation import MediaNavigation
from web_assistant import WebSession,BrowserBackend
from web_browse import BrowseCursor
from settings_model import SettingsSession,DEFAULTS
import companion as app

class MediaNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.player=Mock(kind=None);self.hub=MediaHub(self.temp.name,player=self.player,clock=lambda:datetime(2026,10,12,12))
        self.hub.stations=[{'name':'Alpha','url':'https://radio.test/a'},{'name':'Bravo','url':'https://radio.test/b'},{'name':'Another','url':'https://radio.test/c'}];self.hub.station_index=0
        self.hub.shows=[{'name':'History One','feed':'https://pod.test/1'},{'name':'Science Two','feed':'https://pod.test/2'}];self.hub.show_index=0
        self.nav=MediaNavigation(self.hub)
    def test_station_keyboard_actions_are_short_and_voice_menu_keeps_legacy_actions(self):
        self.nav.enter('radio',keyboard=True)
        self.assertEqual([a.label for a in self.nav.actions],['Add to favorites','Record current station','Schedule recording','Pause audio','Play','Stop recording','Cancel scheduled recordings'])
        self.nav.command('radio menu')
        self.assertIn('Resume audio',[a.label for a in self.nav.actions]);self.assertIn('Search stations',[a.label for a in self.nav.actions])
        self.nav.keyboard('Tab');self.nav.keyboard('Tab')
        self.assertEqual(len(self.nav.actions),7)
        self.nav.index=4;self.nav.keyboard('Enter');self.player.open.assert_called_with('https://radio.test/a','Alpha','radio')
        self.nav.keyboard('Escape');self.assertIn('Search stations',[a.label for a in self.nav.actions])

    def test_keyboard_play_starts_focused_station_and_resumes_only_the_same_station(self):
        self.nav.enter('radio',keyboard=True)
        for kind in ('radio','podcast'):
            self.player.kind=kind;self.hub.current_station=self.hub.stations[1];self.hub.station_index=0
            self.nav.index=4;self.nav.keyboard('Enter')
            self.assertEqual(self.player.open.call_args.args,('https://radio.test/a','Alpha','radio'))
        self.player.kind='radio';self.hub.current_station=self.hub.stations[0]
        self.player.control.reset_mock();self.nav.index=4;self.nav.keyboard('Enter')
        self.player.control.assert_called_once_with('play')
        self.nav.command('resume audio');self.assertIn('Resume audio',[a.label for a in self.nav.actions])

    def test_keyboard_cancel_schedule_opens_list_and_enter_requests_confirmation(self):
        self.hub._save('radio-schedule.json',[{'id':'one','station':self.hub.stations[0],'when':'2026-10-13T20:00:00','minutes':30}])
        self.nav.enter('radio',keyboard=True);self.nav.index=6;self.nav.keyboard('Enter')
        self.assertEqual(self.nav.view,'scheduled');self.assertEqual(len(self.nav.items()),1)
        self.assertIn('Yes or no',self.nav.keyboard('Enter'))
        self.nav.keyboard('y');self.assertEqual(self.nav.items(),[])

    def test_actions_results_letters_boundaries_and_activation(self):
        self.assertIn('Radio menu',self.nav.enter('radio'))
        self.nav.keyboard('Escape');self.nav.keyboard('Letter:l');self.assertEqual(self.nav.actions[self.nav.index].label,'List stations')
        self.nav.keyboard('Tab');self.nav.keyboard('Home');self.nav.keyboard('Letter:a');self.assertEqual(self.hub.station_index,2)
        self.assertIn('End of matching items',self.nav.keyboard('Letter:a'));self.assertEqual(self.hub.station_index,2)
        self.nav.keyboard('End');self.nav.keyboard('Enter');self.player.open.assert_called_with('https://radio.test/c','Another','radio')
        self.assertEqual(self.nav.keyboard('Escape').split('.')[0],'Station actions')
        self.assertIn('Radio menu',self.nav.keyboard('Escape'))
        self.assertEqual(self.nav.keyboard('Escape'),'__main__')
    def test_search_form_preserves_literal_text_and_voice_interoperates(self):
        self.nav.enter('radio');self.nav.keyboard('Ctrl+F')
        with patch.object(self.hub,'search_radio',return_value='Found stations') as search:
            self.nav.field_value('News & Music','Tab');self.assertEqual(self.nav.field_index,1)
            # The name keeps its capitals; names are matched without regard to capitals.
            self.nav.command('okay');search.assert_called_once_with('name','News & Music')
        self.nav.enter('podcast');self.nav.command('Search podcasts')
        with patch.object(self.hub,'search_podcasts',return_value='Found podcasts') as search:
            self.nav.command('History');search.assert_called_once_with('History');self.assertIsNone(self.nav.form)
    def test_choice_database_and_all_browse_forms(self):
        self.nav.open_form('source');self.nav.keyboard('Letter:r');self.assertEqual(self.nav.form['fields'][0]['value'],'radio browser')
        self.nav.keyboard('End');self.assertEqual(self.nav.form['fields'][0]['value'],'radio sure');self.nav.keyboard('Escape')
        for field in ('genre','country','state','city'):
            self.nav.open_form(field)
            with patch.object(self.hub,'search_radio',return_value='Found') as search:
                self.nav.field_value('Jazz','Enter');search.assert_called_once_with(field,'Jazz')
    def test_schedule_fields_validate_then_record_selected_station(self):
        self.nav.select_item(1);self.nav.open_form('schedule')
        self.nav.keyboard('Down');self.nav.keyboard('Tab');self.nav.field_value('25 PM','Tab');self.nav.field_value('45','Tab');self.nav.keyboard('Space')
        with patch.object(self.hub,'schedule',return_value='Scheduled') as schedule:
            self.assertIn('Enter a time',self.nav.submit_form());schedule.assert_not_called()
            self.nav.select_field(1);self.nav.field_value('8:30 PM','Tab');self.nav.keyboard('Tab');self.nav.keyboard('Tab');self.assertEqual(self.nav.field_index,4)
            self.assertEqual(self.nav.keyboard('Enter'),'Scheduled');schedule.assert_called_once_with(self.hub.stations[1],datetime(2026,10,13,20,30),45,True)
        self.nav.open_form('record')
        with patch.object(self.hub,'record_now',return_value='Recording') as record:
            self.nav.field_value('241','Enter');record.assert_not_called()
            self.nav.field_value('15','Enter');record.assert_called_once_with(self.hub.stations[1],15)
    def test_preset_confirmation_y_n_never_deletes_wrong_item(self):
        self.hub.command('save preset',section='radio');self.nav.run('list presets')
        self.nav.keyboard('Delete');self.assertIsNotNone(self.nav.pending)
        self.nav.keyboard('n');self.assertEqual(len(self.hub.presets()),1)
        self.nav.keyboard('Delete');self.nav.keyboard('y');self.assertEqual(self.hub.presets(),[])
    def test_episode_subscribe_download_policy_and_offline_lists(self):
        self.nav.enter('podcast');self.nav.run('list shows')
        with patch.object(self.hub,'_feed_episodes',return_value=[{'title':'Episode','url':'https://pod.test/audio','show':'History One'}]),patch.object(self.hub,'start_podcast_refresh'):
            self.nav.keyboard('Enter');self.assertEqual(self.nav.view,'episodes')
            self.nav.run('subscribe');self.nav.open_form('automatic');self.nav.field_value('3','Enter')
            self.assertIsNone(self.nav.form);self.assertEqual(self.hub._load('podcast-subscriptions.json',[])[0]['download_limit'],3)
        with patch.object(self.hub,'download_episode',return_value='Downloading') as download:
            self.nav.keyboard('Ctrl+D');download.assert_called_once_with(1)
        folder=self.hub._path('Podcast Downloads');folder.mkdir();(folder/'Episode.mp3').write_bytes(b'fixture')
        self.nav.run('list downloads');self.nav.keyboard('Enter');self.assertEqual(self.player.open.call_args.args[2],'podcast')
    def test_schedule_and_subscription_removal_confirm_identity(self):
        self.hub._save('radio-schedule.json',[{'id':'one','station':self.hub.stations[0],'when':'2026-10-13T20:00:00','minutes':30}])
        self.nav.run('list scheduled recordings');self.nav.keyboard('Delete');self.nav.keyboard('n');self.assertEqual(len(self.nav.items()),1)
        self.nav.keyboard('Delete');self.nav.keyboard('y');self.assertEqual(self.nav.items(),[])
        self.hub._save('podcast-subscriptions.json',[{**self.hub.shows[0],'download_limit':'manual'}]);self.nav.enter('podcast');self.nav.run('list subscriptions');self.nav.keyboard('Delete');self.nav.keyboard('y');self.assertEqual(self.hub._load('podcast-subscriptions.json',[]),[])
    def test_audio_keyboard_and_voice_share_pause_seek_and_chapters(self):
        self.player.kind='podcast';self.nav.enter('podcast');self.nav.keyboard('Space');self.player.control.assert_called_with('toggle')
        self.nav.keyboard('Space');self.player.control.assert_called_with('toggle')
        self.nav.keyboard('Ctrl+Left');self.player.control.assert_called_with('seek',seconds=-15)
        self.nav.command('forward 30 seconds');self.player.control.assert_called_with('seek',seconds=30)
        self.nav.keyboard('Ctrl+S');self.player.control.assert_called_with('stop')
    def test_companion_keys_voice_and_context_use_same_results(self):
        with patch.object(app,'MEDIA_HUB',self.hub),patch.object(app,'MEDIA_SECTION','radio'),patch.object(app,'MEDIA_NAV',self.nav),patch.object(app,'speak'),patch.object(app,'account_setup',False),patch.object(app,'pending_website',None),patch.object(app,'SYNTH_PICK_INDEX',None),patch.object(app,'VOICE_PICK_INDEX',None):
            self.assertEqual(app.handle('radio','awake'),'media');app.navigation_key('Tab','media');app.navigation_key('End','media')
            self.assertEqual(self.hub.station_index,2);app.handle('that one','media');self.player.open.assert_called_with('https://radio.test/c','Another','radio')
            app.navigation_key('Ctrl+F','media');self.assertTrue(app.app_context('media')['media_input'])
            app.navigation_key('Escape','media');self.assertFalse(app.app_context('media').get('media_input',False))

class BrowserLifecycleTests(unittest.TestCase):
    def test_leave_closes_owned_context_and_reopen_retained_page(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();backend.snapshot.return_value={'title':'Fixture','url':'https://fixture.test','text':'Hello','controls':[],'elements':[]};backend.page_ready=True;backend.document_stamp.return_value={}
            session=WebSession(folder,backend);session._refresh();session.leave(True);backend.close.assert_not_called()
            self.assertIn('Page loaded',session.resume());backend.refocus.assert_called_once()
            session.leave(False);backend.close.assert_called_once();self.assertIsNone(session.snapshot);self.assertEqual(session.reading.text,'')
    def test_escape_and_menu_resume_keep_preference_default_off(self):
        self.assertFalse(DEFAULTS['web_keep_open']);settings=SettingsSession({},{});settings.set('web_keep_open','on');self.assertTrue(settings.values['web_keep_open'])
        session=Mock(snapshot=True,list_focus=None,keyboard_field=None,pending=None,submit_pending=None);session.resume.return_value='Page loaded: Saved page.'
        from browser_menu import BrowserMenu
        session.menu=BrowserMenu(tempfile.gettempdir())
        with patch.object(app,'web_session',session),patch.object(app,'PREFERENCES',{'web_keep_open':False}),patch.object(app,'speak'),patch.object(app,'pending_website',None):
            self.assertEqual(app.handle(app.navigation_key('Escape','web'),'web'),'browser_menu');session.leave.assert_called_with(False)
            app.PREFERENCES['web_keep_open']=True;self.assertEqual(app.handle('web browser','awake'),'browser_menu');session.resume.assert_not_called()
            self.assertEqual(app.handle('resume where you left off','browser_menu'),'web');session.resume.assert_called_once()
    def test_alt_f4_overrides_all_module_and_pending_confirmation_states(self):
        for mode in ('awake','sleep','web','media','email_draft','settings','power_confirm','exit_confirm','document_save'):
            self.assertEqual(app.navigation_key('Alt+F4',mode),'exit companion')
    def test_heading_caret_ack_and_dynamic_snapshot_do_not_repeat_nested_heading(self):
        snapshot={'elements':[{'key':'h1','role':'heading','label':'First','start':0,'end':20},{'key':'a1','role':'link','label':'First','start':0,'end':20},{'key':'h2','role':'heading','label':'Second','start':21,'end':40},{'key':'a2','role':'link','label':'Second','start':21,'end':40}]}
        cursor=BrowseCursor(snapshot);self.assertIn('First',cursor.move('h'));cursor.set_position(0);self.assertIn('Second',cursor.move('h'));cursor.set_position(21);self.assertIn('First',cursor.move('h',True));cursor.set_position(0);self.assertIn('No previous',cursor.move('h',True))
    def test_page_loaded_message_has_no_element_totals(self):
        from test_web_assistant import FakeBackend
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend());session.auto_read=False;message=session.open('example.com');self.assertIn('Page loaded',message);self.assertNotIn('headings',message);self.assertNotIn('links',message)

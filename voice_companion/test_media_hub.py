"""Release checks for live catalog fallback and offline media workflows."""
import io
import contextlib
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from media_hub import MediaHub, IPRD_CATALOG, _interval_seconds, _episode_count
from media_player import MediaPlayer, PLAYER_HTML


class MediaHubTests(unittest.TestCase):
    def test_radio_presets_save_play_navigate_and_confirm_delete(self):
        self.hub.stations = [{'name':'WJLB 97.9','url':'https://radio.example/wjlb'},
                             {'name':'WGN','url':'https://radio.example/wgn'}]
        self.hub.station_index = 0
        self.assertIn('Saved preset', self.hub.command('save preset', section='radio'))
        self.assertIn('already exists', self.hub.command('save preset', section='radio'))
        self.hub.station_index = 1
        self.assertIn('Saved preset', self.hub.command('save preset as News', section='radio'))
        self.assertIn('WJLB', self.hub.command('list presets', section='radio'))
        for phrase in ('preset list', 'list favorites', 'favorites list', 'favorite list'):
            self.assertIn('WJLB', self.hub.command(phrase, section='radio'))
        self.assertIn('news', self.hub.command('next', section='radio'))
        self.hub.player.open = lambda *args: None
        self.assertIn('Playing preset', self.hub.command('that one', section='radio'))
        self.assertEqual(self.hub.current_station['url'], 'https://radio.example/wgn')
        self.assertIn('Say yes or no', self.hub.command('delete preset', section='radio'))
        self.assertIn('canceled', self.hub.command('no', section='radio'))
        self.assertEqual(len(self.hub.presets()), 2)
        self.hub.command('delete preset WJLB 97.9', section='radio')
        self.assertIn('Deleted preset', self.hub.command('yes', section='radio'))
        self.assertEqual([p['name'] for p in self.hub.presets()], ['news'])
        reopened = MediaHub(self.temp.name, player=self.hub.player)
        self.assertIn('news', reopened.command('list presets', section='radio'))

    def test_all_save_phrases_use_same_radio_preset_action(self):
        self.hub.stations = [{'name':'First','url':'https://radio.example/one'}]
        self.hub.station_index = 0
        for phrase in ('add preset as One', 'add favorite as Two',
                       'save preset as Three', 'save favorite as Four'):
            self.assertIn('Saved preset', self.hub.command(phrase, section='radio'))
        self.assertEqual([item['name'] for item in self.hub.presets()],
                         ['one','two','three','four'])
        self.assertIn('Saved preset', self.hub.command('add preset', section='radio'))
        for phrase in ('add favorite', 'save preset', 'save favorite'):
            self.assertIn('already exists', self.hub.command(phrase, section='radio'))

    def test_preset_voice_commands_route_from_main_menu(self):
        import companion
        self.hub.stations = [{'name':'WGN', 'url':'https://radio.example/wgn'}]
        self.hub.station_index = 0
        with patch.object(companion, 'MEDIA_HUB', self.hub), patch.object(companion, 'speak') as speech:
            self.assertEqual(companion.handle('add favorite', 'awake'), 'media')
            self.assertIn('Saved preset', speech.call_args.args[0])
            self.assertEqual(companion.handle('save favorite', 'awake'), 'media')
            self.assertIn('already exists', speech.call_args.args[0])
            self.assertEqual(companion.handle('list presets', 'awake'), 'media')
            self.assertIn('WGN', speech.call_args.args[0])
            self.assertEqual(companion.handle('favorites list', 'awake'), 'media')
            self.assertIn('WGN', speech.call_args.args[0])
            self.assertEqual(companion.handle('list favorites', 'media'), 'media')
            self.assertIn('WGN', speech.call_args.args[0])
            companion.handle('delete preset', 'media')
            self.assertIn('Say yes or no', speech.call_args.args[0])
            companion.handle('no', 'media')
            self.assertEqual(len(self.hub.presets()), 1)

    def test_podcast_search_browse_open_and_subscribe_selected_feed(self):
        self.hub.fetch_json=lambda url: {'results':[
            {'collectionName':'History One','feedUrl':'https://pod.example/one','primaryGenreName':'History'},
            {'collectionName':'History Two','feedUrl':'https://pod.example/two','primaryGenreName':'History'}]}
        self.assertIn('What podcast',self.hub.command('search podcast',section='podcast'))
        self.assertIn('History One',self.hub.command('history',section='podcast'))
        self.assertEqual(self.hub.show_index,0)
        self.assertIn('History Two',self.hub.command('next',section='podcast'))
        with patch.object(self.hub,'_feed_episodes',return_value=[
                {'title':'Episode A','url':'https://pod.example/audio.mp3','show':'History Two'}]):
            self.assertIn('Episode A',self.hub.command('confirm',section='podcast'))
        self.assertEqual(self.hub.current_feed,'https://pod.example/two')
        with patch.object(self.hub,'start_podcast_refresh'):
            self.assertIn('History Two',self.hub.command('subscribe',section='podcast'))
        self.assertEqual(self.hub._load('podcast-subscriptions.json',[])[0]['feed'],'https://pod.example/two')
        self.hub.command('back to podcasts',section='podcast')
        self.assertIn('History One',self.hub.command('previous',section='podcast'))
        self.assertIn('History One',self.hub.command('search history',section='podcast'))
        self.assertIn('History One',self.hub.command('search podcast history',section='podcast'))
        self.assertIn('History One',self.hub.command('browse podcasts category history',section='podcast'))

    def test_podcast_companion_routes_confirmation_and_next(self):
        import companion
        self.hub.shows=[{'name':'First','feed':'https://pod.example/first'},
                        {'name':'Second','feed':'https://pod.example/second'}]
        self.hub.show_index=0
        with patch.object(companion,'MEDIA_HUB',self.hub),patch.object(companion,'MEDIA_SECTION','podcast'), \
             patch.object(companion,'speak') as speech, \
             patch.object(self.hub,'open_podcast',return_value='Opened second') as opened:
            self.assertEqual(companion.handle('next','media'),'media')
            self.assertIn('Second',speech.call_args.args[0])
            self.assertEqual(companion.handle('that one','media'),'media')
            opened.assert_called_with(2)
            self.assertEqual(companion.handle('okay','media'),'media')
            opened.assert_called_with(2)

    def test_episode_browsing_and_short_play_after_search_or_subscription(self):
        self.hub.shows=[{'name':'History','feed':'https://pod.example/history'}]
        self.hub.show_index=0
        episodes=[{'title':'First episode','url':'https://pod.example/1.mp3','show':'History'},
                  {'title':'Second episode','url':'https://pod.example/2.mp3','show':'History'}]
        with patch.object(self.hub,'_feed_episodes',return_value=episodes), \
             patch.object(self.hub,'play_episode',side_effect=lambda number: 'Played '+str(number)) as play:
            self.assertIn('First episode',self.hub.command('that one',section='podcast'))
            self.assertNotIn('Second episode',self.hub.command('list episodes',section='podcast'))
            self.assertIn('Second episode',self.hub.command('next',section='podcast'))
            for phrase in ('that one','confirm','confirm that','okay','play 2','play episode 2'):
                self.assertEqual(self.hub.command(phrase,section='podcast'),'Played 2',phrase)
            self.assertEqual(play.call_count,6)
            self.assertIn('First episode',self.hub.command('previous',section='podcast'))
            self.assertEqual(self.hub.command('play 1',section='podcast'),'Played 1')
        self.hub._save('podcast-subscriptions.json',[{**self.hub.shows[0],'episodes':episodes}])
        self.hub.subscriptions()
        with patch.object(self.hub,'_feed_episodes',return_value=episodes):
            self.assertIn('First episode',self.hub.command('okay',section='podcast'))

    def test_auto_download_uses_audio_request_and_reports_host_error(self):
        from urllib.error import HTTPError
        episode={'title':'Episode','url':'https://pod.example/episode.mp3','show':'History',
                 'feed':'https://pod.example/rss.xml'}
        target=Path(self.temp.name)/'episode.mp3'
        class Stream(io.BytesIO):
            headers={'Content-Type':'audio/mpeg'}
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
        with patch('media_hub.request.urlopen',return_value=Stream(b'audio')) as open_url:
            self.assertTrue(self.hub._download_worker(episode,target,target.with_suffix('.part'),announce=False))
        sent=open_url.call_args.args[0]
        self.assertIn('Mozilla',sent.get_header('User-agent'))
        self.assertEqual(sent.get_header('Referer'),episode['feed'])
        episode['url']='https://pod.example/retry.mp3'
        with patch('media_hub.request.urlopen',side_effect=[
                HTTPError(episode['url'],403,'Forbidden',{},None),Stream(b'audio')]) as retry:
            self.assertTrue(self.hub._download_worker(episode,target,target.with_suffix('.part'),announce=False))
        self.assertIsNone(retry.call_args.args[0].get_header('Referer'))
        episode['url']='https://pod.example/blocked.mp3'
        with patch('media_hub.request.urlopen',side_effect=HTTPError(episode['url'],403,'Forbidden',{},None)), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.hub._download_worker(episode,target,target.with_suffix('.part'),announce=False))
        self.assertIn('HTTP 403',self.hub.download_errors[episode['url']])

    def test_podcast_list_aliases_open_subscriptions(self):
        self.hub._save('podcast-subscriptions.json', [{'name':'Saved History','feed':'https://pod.example/history'}])
        self.hub.shows=[{'name':'Search Result','feed':'https://pod.example/search'}]
        for phrase in ('subscription list','subscriptions list','podcast list','podcasts list',
                       'list podcast','list podcasts','list subscriptions'):
            self.assertIn('Saved History', self.hub.command(phrase, section='podcast'))
            self.assertEqual(self.hub.shows[0]['name'], 'Saved History')

    def test_pause_has_one_spoken_confirmation(self):
        self.assertNotIn('aria-live',PLAYER_HTML)
        self.assertNotIn('role="status"',PLAYER_HTML)
        self.hub.player.kind='podcast'
        self.hub.player.control=lambda action,seconds=15,position=None: 0
        self.assertEqual(self.hub.command('pause',section='podcast'),'Audio paused.')

    def test_player_ducks_and_restores_volume_around_speech(self):
        class Page:
            def __init__(self): self.values=[]
            def is_closed(self): return False
            def evaluate(self, script, value): self.values.append(value)
        player=MediaPlayer(self.temp.name)
        player.page=Page();player.kind='radio'
        player.set_ducked(True)
        player.set_ducked(True)
        player.set_ducked(False)
        self.assertEqual(player.page.values,[True,False])
        import companion
        hub=MediaHub(self.temp.name,player=player)
        with patch.object(companion,'MEDIA_HUB',hub),patch.object(companion,'SPEECH_PAUSED',False), \
             patch.object(companion,'speech_busy',return_value=True):
            companion.update_audio_ducking()
            self.assertTrue(player.ducked)
        with patch.object(companion,'MEDIA_HUB',hub),patch.object(companion,'SPEECH_PAUSED',False), \
             patch.object(companion,'speech_busy',return_value=False):
            companion.update_audio_ducking()
            self.assertFalse(player.ducked)

    def test_station_play_does_not_require_separate_network_probe(self):
        self.hub.stations=[{'name':'WJLB 97.9','url':'https://radio.example/live'}]
        self.hub.player.open=lambda *args: None
        with patch('media_hub.request.urlopen',side_effect=AssertionError('preflight must not run')):
            self.assertIn('Playing WJLB',self.hub.command('play station 1',section='radio'))

    def test_short_audio_controls_across_modes_and_sources(self):
        import companion
        class Player:
            kind='radio'
            calls=[]
            def control(self,action,seconds=15,position=None):
                self.calls.append(action)
                return 0
        player=Player()
        self.hub.player=player
        with patch.object(companion,'MEDIA_HUB',self.hub),patch.object(companion,'speak') as say:
            for kind in ('radio','podcast'):
                player.kind=kind
                for mode in ('media','document','email_draft','web'):
                    for command,action in (('pause','pause'),('play','play'),('resume','play'),('stop','stop')):
                        self.assertEqual(companion.handle(command,mode),mode)
                        self.assertEqual(player.calls[-1],action,(kind,mode,command))
                        self.assertIn('audio',say.call_args.args[0].lower())
            self.assertEqual(companion.handle('stop talking','media'),'media')
            self.assertEqual(player.calls[-1],'stop')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.notices = []
        self.now = datetime(2026, 9, 29, 10, 0)
        self.hub = MediaHub(self.temp.name, notify=self.notices.append, clock=lambda: self.now)

    def test_search_merges_live_and_saved_and_hides_failed_stream(self):
        self.hub.fetch_json = lambda url: [
            {'name':'Jazz One','url_resolved':'https://radio.example/one','country':'US'},
            {'name':'Jazz Broken','url':'https://radio.example/broken','lastcheckok':0}]
        self.hub._save('radio-iprd-stations.json',[
            {'name':'Jazz Two','url':'https://radio.example/two','country':'United States','source':'IPRD'}])
        self.assertIn('Jazz One',self.hub.search_radio('name','Jazz'))
        self.assertIn('Jazz Two',self.hub.command('next',section='radio'))
        self.assertEqual([s['name'] for s in self.hub.stations],['Jazz One','Jazz Two'])
        self.hub._mark_bad(self.hub.stations[0])
        self.hub.search_radio('name','Jazz')
        self.assertEqual([s['name'] for s in self.hub.stations],['Jazz Two'])

    def test_directory_mirror_fallback_and_country_alias(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            if 'all.api.' in url: raise OSError('unavailable')
            return []
        self.hub.fetch_json=fetch
        self.hub._save('radio-iprd-stations.json',[
            {'name':'Seattle Radio','url':'https://radio.example/seattle','country':'United States'}])
        self.assertIn('Seattle Radio',self.hub.search_radio('country','US'))
        self.assertEqual(len(calls),2)

    def test_spoken_source_preference_persists_and_limits_search(self):
        self.hub._save('radio-iprd-stations.json',[
            {'name':'Jazz from IPRD','url':'https://radio.example/iprd','tags':'Jazz'}])
        self.assertIn('IPRD',self.hub.command('choose station database IPRD'))
        self.hub.fetch_json=lambda url: self.fail('Radio Browser must not be queried')
        another=MediaHub(self.temp.name)
        another.fetch_json=self.hub.fetch_json
        self.assertEqual(another.source(),'iprd')
        self.assertIn('Jazz from IPRD',another.search_radio('genre','Jazz'))
        self.assertIn('not connected',another.command('choose station database TuneIn'))
        self.assertEqual(another.source(),'iprd')

    def test_iprd_refresh_honors_24_hour_cache(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            self.assertEqual(url,IPRD_CATALOG)
            return {'stations':[{'name':'New Radio','country':'US','genres':['Jazz'],
                     'streams':[{'url':'https://radio.example/new','format':'mp3'}]}]}
        self.hub.fetch_json=fetch
        self.assertIn('refreshed',self.hub.refresh_iprd())
        self.assertIn('last 24 hours',self.hub.refresh_iprd())
        self.assertEqual(len(calls),1)

    def test_radiosure_import_is_local_and_searchable(self):
        source=Path(self.temp.name)/'stations.rsd'
        source.write_text('Old Jazz\t1\tJazz\tUS\tEnglish\thttps://radio.example/old\n',encoding='utf-8')
        self.assertIn('Imported 1',self.hub.import_radiosure(source))
        self.hub.fetch_json=lambda url: []
        self.assertIn('Old Jazz',self.hub.search_radio('genre','Jazz'))

    def test_due_schedule_starts_recording_before_catalog_fetch(self):
        station={'name':'News','url':'https://radio.example/news'}
        self.hub._save('radio-schedule.json',[{'station':station,'when':self.now.isoformat(),
                                               'minutes':15,'repeat':False}])
        order=[]
        def record(*args): order.append('record'); return 'Recording started.'
        def refresh(*args,**kwargs): order.append('refresh')
        self.hub.record_now=record
        self.hub.refresh_iprd=refresh
        self.hub.tick()
        self.assertEqual(order,['record','refresh'])
        self.assertEqual(self.hub._load('radio-schedule.json',[]),[])

    def test_podcast_subscription_and_offline_download(self):
        self.hub.fetch_json=lambda url: {'results':[{'collectionName':'History','feedUrl':'https://pod.example/feed',
                                                     'primaryGenreName':'History'}]}
        self.assertIn('History',self.hub.search_podcasts('history'))
        with patch.object(self.hub,'start_podcast_refresh'):
            self.assertIn('Subscribed',self.hub.subscribe(1))
        self.assertIn('History',self.hub.subscriptions())
        self.hub.episodes=[{'show':'History','title':'Episode 1','url':'https://pod.example/episode.mp3'}]
        class Stream(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self,*args): self.close()
        with patch('media_hub.request.urlopen',return_value=Stream(b'audio')):
            target=Path(self.temp.name)/'Podcast Downloads'/'History - Episode 1.mp3'
            target.parent.mkdir()
            self.hub._download_worker(self.hub.episodes[0],target,target.with_suffix('.mp3.part'))
        self.assertEqual(target.read_bytes(),b'audio')
        self.assertTrue(any(n.startswith('Downloaded Episode 1.') for n in self.notices))

    def test_flexible_seek_and_chapter_episode_navigation(self):
        class Player:
            kind='podcast'
            position=120
            calls=[]
            def open(self,url,title,kind): self.calls.append(('open',url));self.kind=kind
            def control(self,action,seconds=15,position=None):
                self.calls.append((action,seconds,position))
                if action=='seek': self.position=max(0,position if position is not None else self.position+seconds)
                return self.position
        player=Player()
        self.hub.player=player
        self.hub.episodes=[{'show':'History','title':'First','url':'https://pod.example/1',
                            'chapters_url':'https://pod.example/chapters'},
                           {'show':'History','title':'Second','url':'https://pod.example/2'}]
        self.hub.fetch_json=lambda url: {'chapters':[{'startTime':0,'title':'Opening'},
                                                      {'startTime':600,'title':'Interview'}]}
        self.assertIn('Playing First',self.hub.play_episode(1))
        self.assertIn('Moved forward',self.hub.command('fast forward 30 minutes'))
        self.assertEqual(player.position,1920)
        self.assertIn('Moved back',self.hub.command('rewind one hour'))
        self.assertEqual(player.position,0)
        self.assertIn('Interview',self.hub.command('next chapter'))
        self.assertEqual(player.position,600)
        self.assertIn('Playing Second',self.hub.command('next episode'))
        self.assertIn('Audio paused',self.hub.command('pause podcast'))
        self.assertIn('Audio stopped',self.hub.command('stop podcast'))

    def test_spoken_seek_intervals_and_invalid_request(self):
        self.assertEqual(_interval_seconds('15 seconds'),15)
        self.assertEqual(_interval_seconds('for 30 minutes'),1800)
        self.assertEqual(_interval_seconds('one hour and thirty minutes'),5400)
        self.assertEqual(_interval_seconds('2 hours 15 minutes 5 seconds'),8105)
        self.assertIsNone(_interval_seconds('soon'))
        self.assertIsNone(_interval_seconds('zero minutes'))

    def test_companion_routes_audio_controls_even_outside_media_mode(self):
        import companion
        self.hub.player.kind='podcast'
        self.hub.player.control=lambda action,seconds=15,position=None: 12
        with patch.object(companion,'MEDIA_HUB',self.hub),patch.object(companion,'speak') as speak:
            self.assertEqual(companion.handle('Fast forward 30 minutes.', 'awake'),'awake')
            self.assertIn('Moved forward',speak.call_args.args[0])
            self.assertEqual(companion.handle('Stop podcast', 'document'),'document')
            self.assertEqual(speak.call_args.args[0],'Audio stopped.')

    def test_subscription_policy_accepts_spoken_and_exact_counts(self):
        self.assertEqual(_episode_count('two hundred five'),205)
        for value in ('1','5','10','50','100','205','all','manual'):
            self.assertIsNotNone(_episode_count(value))
        self.assertIsNone(_episode_count('10001'))
        self.hub.shows=[{'name':'History','feed':'https://pod.example/feed'}]
        with patch.object(self.hub,'start_podcast_refresh'):
            self.hub.subscribe(1)
            self.hub.current_feed='https://pod.example/feed'
            self.assertIn('newest 205',self.hub.command('auto download 205 episodes'))
            self.assertIn('newest 5',self.hub.command('auto download 5 episodes for podcast 1'))
            self.assertIn('newest 100',self.hub.command('auto-download 100'))
            self.assertIn('manually',self.hub.command('manual downloads'))
        self.assertIn('downloads manual',self.hub.subscriptions())

    def test_refresh_updates_feed_and_enforces_auto_limit_without_touching_manual_files(self):
        feed='https://pod.example/feed'
        show={'name':'History','feed':feed,'download_limit':2,'episodes':[]}
        self.hub._save('podcast-subscriptions.json',[show])
        episodes=[{'show':'History','title':f'Episode {i}','url':f'https://pod.example/{i}.mp3',
                   'published':10-i} for i in (1,2,3)]
        self.hub._feed_episodes=lambda item: episodes
        manual=Path(self.temp.name)/'Podcast Downloads'/'Manual.mp3'
        manual.parent.mkdir();manual.write_bytes(b'manual')
        def download(episode,target,temp,announce=True):
            target.write_bytes(b'audio');return True
        self.hub._download_worker=download
        self.hub.refresh_subscriptions()
        saved=self.hub._load('podcast-subscriptions.json',[])[0]
        self.assertEqual(len(saved['episodes']),3)
        manifest=self.hub._load('podcast-auto-files.json',{})[feed]
        self.assertEqual(set(manifest),{episodes[0]['url'],episodes[1]['url']})
        self.assertTrue(manual.exists())
        episodes.insert(0,{'show':'History','title':'New','url':'https://pod.example/new.mp3','published':11})
        self.hub.refresh_subscriptions()
        manifest=self.hub._load('podcast-auto-files.json',{})[feed]
        self.assertEqual(set(manifest),{episodes[0]['url'],episodes[1]['url']})
        self.assertTrue(manual.exists())
        self.assertTrue(any('1 new episodes' in notice for notice in self.notices))

    def test_failed_refresh_retains_saved_episodes_and_files(self):
        show={'name':'History','feed':'https://pod.example/feed','download_limit':'all',
              'episodes':[{'title':'Saved','url':'https://pod.example/1.mp3'}]}
        self.hub._save('podcast-subscriptions.json',[show])
        self.hub._feed_episodes=lambda item: (_ for _ in ()).throw(OSError('offline'))
        self.assertIn('0 of 1',self.hub.refresh_subscriptions())
        self.assertEqual(self.hub._load('podcast-subscriptions.json',[])[0]['episodes'],show['episodes'])


if __name__=='__main__': unittest.main()

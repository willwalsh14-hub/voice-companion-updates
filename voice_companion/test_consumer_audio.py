"""Decode and control real fixture audio in the production media surface."""
import io,os,sys,tempfile,time,unittest,wave
from pathlib import Path
from playwright.sync_api import sync_playwright
from media_player import MediaPlayer,PLAYER_HTML
from media_hub import MediaHub
from media_navigation import MediaNavigation

class ConsumerAudioTests(unittest.TestCase):
    def test_local_video_then_audio_switch_keeps_playback_controls_connected(self):
        self.page.goto((self.folder/'audio-player.html').as_uri())
        data=self.page.evaluate('''async()=>{
            const canvas=document.createElement('canvas');canvas.width=32;canvas.height=24;
            const ctx=canvas.getContext('2d');ctx.fillRect(0,0,32,24);
            const stream=canvas.captureStream(10);const parts=[];
            const recorder=new MediaRecorder(stream,{mimeType:'video/webm'});
            const result=new Promise(resolve=>{recorder.ondataavailable=e=>parts.push(e.data);recorder.onstop=async()=>resolve(Array.from(new Uint8Array(await new Blob(parts).arrayBuffer())));});
            recorder.start();const timer=setInterval(()=>{ctx.fillStyle='blue';ctx.fillRect(0,0,32,24);},50);
            await new Promise(resolve=>setTimeout(resolve,1000));clearInterval(timer);recorder.stop();
            return await result;
        }''')
        path=self.folder/'sample.webm';path.write_bytes(bytes(data))
        self.player.open(path.as_uri(),'Local video','local')
        self.assertEqual(self.page.evaluate("document.getElementById('audio').tagName"),'VIDEO')
        self.assertFalse(self.player.status()['paused'])
        self.page.evaluate("document.querySelectorAll('button')[1].click()")
        self.assertTrue(self.player.status()['paused'])
        audio=self.folder/'sample.wav';audio.write_bytes(self.wav)
        self.player.open(audio.as_uri(),'Local audio','local')
        self.assertEqual(self.page.evaluate("document.getElementById('audio').tagName"),'AUDIO')
        self.page.evaluate("document.querySelectorAll('button')[1].click()")
        self.assertTrue(self.player.status()['paused'])

    def test_local_file_playback_volume_speed_pause_seek_and_stop(self):
        with tempfile.TemporaryDirectory() as other:
            path=Path(other)/'sample.wav';path.write_bytes(self.wav)
            self.player.open(path.as_uri(),'Local fixture','local')
            self.assertEqual(self.player.kind,'local')
            self.assertFalse(self.player.status()['paused'])
            self.assertAlmostEqual(self.player.adjust('volume',-.4),.6)
            self.player.set_ducked(True)
            self.assertAlmostEqual(self.page.evaluate("document.getElementById('audio').volume"),.108)
            self.player.set_ducked(False)
            self.assertAlmostEqual(self.page.evaluate("document.getElementById('audio').volume"),.6)
            self.assertEqual(self.player.adjust('speed',1.5),1.5)
            self.player.control('pause');self.assertTrue(self.player.status()['paused'])
            self.assertIsNotNone(self.player.control('seek',position=1))
            self.player.control('play');self.assertFalse(self.player.status()['paused'])
            self.player.control('stop');self.assertTrue(self.player.status()['paused'])
            self.assertLess(self.player.status()['position'],.1)

    def test_local_file_seek_says_jump_place_and_each_end(self):
        from media_player import seek_message
        with tempfile.TemporaryDirectory() as other:
            path=Path(other)/'sample.wav';path.write_bytes(self.wav)
            self.player.open(path.as_uri(),'Local fixture','local')
            self.player.control('pause')
            self.page.wait_for_function('document.getElementById("audio").seekable.length>0',timeout=5000)
            self.player.control('seek',position=1)
            self.assertEqual(seek_message(self.player,1),'Moved forward 1 second. 2 seconds of 4 seconds.')
            self.assertEqual(seek_message(self.player,-1),'Moved back 1 second. 1 second of 4 seconds.')
            self.assertEqual(seek_message(self.player,-15),'Moved back to the start of the track.')
            self.assertLess(self.player.status()['position'],.1)
            self.assertEqual(seek_message(self.player,15),'Moved forward to the end of the track.')
            self.assertAlmostEqual(self.player.status()['position'],4,delta=.1)
            self.player.close()
            self.assertIsNone(seek_message(self.player,15))

    @classmethod
    def setUpClass(cls):
        cls.driver=sync_playwright().start()
        if not Path(cls.driver.firefox.executable_path).is_file():
            cls.driver.stop();raise unittest.SkipTest('Firefox runtime is needed for actual audio checks.')
        cls.browser=cls.driver.firefox.launch(headless=True,firefox_user_prefs={'media.autoplay.default':0},env={**os.environ,'MOZ_DISABLE_CONTENT_SANDBOX':'1','MOZ_DISABLE_RDD_SANDBOX':'1','MOZ_DISABLE_GMP_SANDBOX':'1'})
        data=io.BytesIO()
        with wave.open(data,'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0\0'*16000*4)
        cls.wav=data.getvalue()
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.driver.stop()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);(self.folder/'audio-player.html').write_text(PLAYER_HTML)
        self.context=self.browser.new_context();self.addCleanup(self.context.close)
        self.context.route('https://audio.consumer.test/**',self.audio_route)
        self.page=self.context.new_page();self.page.add_init_script("addEventListener('DOMContentLoaded',()=>{const a=document.querySelector('audio');if(a)a.muted=true})")
        self.player=MediaPlayer(self.folder);self.player.start_timeout_ms=5000;self.player.context=self.context;self.player.page=self.page;self.player.start=lambda:None
        self.hub=MediaHub(self.folder,player=self.player)
    @unittest.skipUnless(sys.platform=='win32','Actual decoding requires the Windows media environment')
    def test_shared_toggle_uses_actual_player_state_for_radio_and_podcasts(self):
        for kind in ('radio','podcast'):
            with self.subTest(kind=kind):
                self.player.open('https://audio.consumer.test/valid','Fixture audio',kind)
                self.assertEqual(self.hub.command('toggle audio'),'Audio paused.')
                self.assertTrue(self.page.evaluate("document.querySelector('audio').paused"))
                self.assertEqual(self.hub.command('toggle audio'),'Playing audio.')
                self.assertFalse(self.page.evaluate("document.querySelector('audio').paused"))
                self.hub.command('pause audio')
                self.assertEqual(self.hub.command('toggle audio'),'Playing audio.')
                self.hub.command('stop audio')

    def audio_route(self,route):
        if route.request.url.endswith('/broken'):
            route.fulfill(status=404,body='Unavailable');return
        headers={'Access-Control-Allow-Origin':'*','Accept-Ranges':'bytes'}
        value=route.request.headers.get('range','')
        if value.startswith('bytes='):
            begin,end=value[6:].split('-');begin=int(begin or 0);end=int(end) if end else len(self.wav)-1
            headers['Content-Range']=f'bytes {begin}-{end}/{len(self.wav)}'
            route.fulfill(status=206,headers=headers,content_type='audio/wav',body=self.wav[begin:end+1])
        else:route.fulfill(headers=headers,content_type='audio/wav',body=self.wav)
    @unittest.skipUnless(sys.platform=='win32','Actual decoding requires the Windows media environment')
    def test_radio_play_pause_resume_stop_and_ducking(self):
        self.hub.stations=[{'name':'Fixture station','url':'https://audio.consumer.test/valid'}];self.hub.station_index=0
        nav=MediaNavigation(self.hub,'radio');nav.keyboard('Enter')
        self.assertEqual(self.player.kind,'radio');self.assertFalse(self.page.locator('#audio').evaluate('(a)=>a.paused'))
        self.player.set_ducked(True);self.assertAlmostEqual(self.page.locator('#audio').evaluate('(a)=>a.volume'),.18)
        self.player.set_ducked(False);self.assertEqual(self.page.locator('#audio').evaluate('(a)=>a.volume'),1)
        nav.command('pause');self.assertTrue(self.page.locator('#audio').evaluate('(a)=>a.paused'))
        nav.command('resume');self.assertFalse(self.page.locator('#audio').evaluate('(a)=>a.paused'))
        nav.command('stop');self.assertTrue(self.page.locator('#audio').evaluate('(a)=>a.paused'))
        self.assertAlmostEqual(self.page.locator('#audio').evaluate('(a)=>a.currentTime'),0,places=2)
    @unittest.skipUnless(sys.platform=='win32','Actual decoding requires the Windows media environment')
    def test_podcast_seek_and_transport_do_not_touch_radio_presets(self):
        self.hub.episodes=[{'title':'Fixture episode','url':'https://audio.consumer.test/valid','show':'Fixture show'}];self.hub.episode_index=0;self.hub.browsing_episodes=True
        nav=MediaNavigation(self.hub,'podcast');nav.keyboard('Enter');nav.command('pause')
        self.page.wait_for_function('document.querySelector("audio").seekable.length>0',timeout=5000)
        self.assertIn('Moved',nav.command('forward 1 second'))
        self.assertAlmostEqual(self.page.locator('#audio').evaluate('(a)=>a.currentTime'),1,delta=.3)
        nav.command('rewind 1 second');nav.command('stop')
        self.assertEqual(self.hub.presets(),[]);self.assertEqual(self.player.kind,'podcast')
    @unittest.skipUnless(sys.platform=='win32','Actual decoding requires the Windows media environment')
    def test_broken_audio_reports_failure_then_next_station_plays(self):
        self.hub.stations=[{'name':'Broken','url':'https://audio.consumer.test/broken'},{'name':'Working','url':'https://audio.consumer.test/valid'}];self.hub.station_index=0
        nav=MediaNavigation(self.hub,'radio')
        self.assertIn('could not start',nav.keyboard('Enter'))
        nav.keyboard('Down');said=nav.keyboard('Enter')
        # On failure, say why the player refused, so one Windows run names the cause.
        self.assertIn('Playing',said,getattr(self.player,'last_start_error',''))
        self.assertEqual(self.player.kind,'radio');self.assertIn('Working',self.page.locator('#title').inner_text())

    def test_stalled_start_and_resume_return_control_instead_of_hanging(self):
        self.page.goto((self.folder/'audio-player.html').as_uri())
        self.page.evaluate('(()=>{document.getElementById("audio").play=()=>new Promise(()=>{});return true})()')
        self.player.start_timeout_ms=150;start=time.monotonic()
        with self.assertRaises(OSError):self.player.open('data:audio/wav;base64,','Stalled','radio')
        self.assertLess(time.monotonic()-start,2);self.assertIsNone(self.player.kind)
        self.player.kind='radio';start=time.monotonic()
        with self.assertRaises(Exception):self.player.control('play')
        self.assertLess(time.monotonic()-start,2)

if __name__=='__main__':unittest.main()

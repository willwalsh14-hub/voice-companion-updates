"""A single controlled audio surface for radio, podcasts, and offline files."""
import os
import sys
from pathlib import Path

PLAYER_HTML = '''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Voice Companion Audio</title><style>body{font:1.25rem system-ui;padding:2rem}
button{font:inherit;margin:.35rem;padding:.6rem}audio{display:block;width:100%;margin:1rem 0}</style>
<h1>Voice Companion Audio</h1><p id="title">No audio selected.</p>
<audio id="audio" controls preload="none"></audio>
<button onclick="audio.play()">Play</button><button onclick="audio.pause()">Pause</button>
<button onclick="audio.pause();audio.currentTime=0">Stop</button>
<button onclick="audio.currentTime=Math.max(0,audio.currentTime-15)">Back 15 seconds</button>
<button onclick="audio.currentTime=Math.min(audio.duration||Infinity,audio.currentTime+15)">Forward 15 seconds</button>
<p id="status"></p><script>
const audio=document.getElementById('audio');
audio.onerror=()=>document.getElementById('status').textContent='This audio could not play.';
audio.onplaying=()=>document.getElementById('status').textContent='Playing.';
audio.onpause=()=>document.getElementById('status').textContent='Paused.';
</script></html>'''


class MediaPlayer:
    def __init__(self, folder):
        self.folder=Path(folder)
        self.playwright=self.context=self.page=None
        self.kind=None
        self.ducked=False
        self.start_timeout_ms=12000

    def start(self):
        if self.page and not self.page.is_closed(): return
        from playwright.sync_api import sync_playwright
        bundled=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'browser-runtime'
        if bundled.exists(): os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(bundled)
        self.folder.mkdir(parents=True,exist_ok=True)
        html=self.folder/'audio-player.html'
        html.write_text(PLAYER_HTML,encoding='utf-8')
        try:
            self.playwright=sync_playwright().start()
            self.context=self.playwright.firefox.launch_persistent_context(
                str(self.folder/'profile'),headless=False,
                firefox_user_prefs={'media.autoplay.default':0})
            self.page=self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.goto(html.as_uri())
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            if self.context: self.context.close()
        finally:
            if self.playwright: self.playwright.stop()
            self.context=self.page=self.playwright=None
            self.kind=None
            self.ducked=False

    def open(self, url, title, kind):
        self.start()
        self.kind=None
        if self.page.url != (self.folder/'audio-player.html').as_uri():
            self.page.goto((self.folder/'audio-player.html').as_uri())
        result=self.page.evaluate('''async ({url,title,timeout}) => {
            const a=document.getElementById('audio');a.pause();a.src=url;
            a.volume=window.voiceCompanionDucked?0.18:1;
            document.getElementById('title').textContent=title;
            document.getElementById('status').textContent='Loading audio.';
            let timer;
            try {
                const started=await Promise.race([a.play().then(()=>true,()=>false),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),timeout)})]);
                if(!started){a.pause();a.removeAttribute('src');a.load();document.getElementById('status').textContent='This audio could not start.';}
                return started;
            } finally {clearTimeout(timer);}
        }''',{'url':url,'title':title,'timeout':self.start_timeout_ms})
        if not result:
            self.kind=None
            raise OSError('This audio format could not start in the player.')
        self.kind=kind

    def set_ducked(self, ducked):
        """Lower radio and podcast playback while Companion speaks."""
        ducked=bool(ducked)
        if self.ducked==ducked: return
        if not self.page or self.page.is_closed() or not self.kind:
            self.ducked=False
            return
        self.page.evaluate('''ducked => {
            window.voiceCompanionDucked=ducked;
            document.getElementById('audio').volume=ducked?0.18:1;
        }''',ducked)
        self.ducked=ducked

    def control(self, action, seconds=15, position=None):
        if not self.page or self.page.is_closed() or not self.kind: return None
        return self.page.evaluate('''async ({action,seconds,position,timeout}) => {
            const a=document.getElementById('audio');
            if(action==='stop'){a.pause();try{a.currentTime=0}catch(e){};return 0}
            if(action==='pause'){a.pause();return a.currentTime}
            if(action==='play'){
                let timer;
                try {
                    const started=await Promise.race([a.play().then(()=>true,()=>false),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),timeout)})]);
                    if(!started){a.pause();throw new Error('This audio could not resume. Choose the station or episode again.');}
                    return a.currentTime;
                }finally{clearTimeout(timer);}
            }
            if(action==='seek'){
                if(!a.seekable.length)return null;
                let p=position===null?a.currentTime+seconds:position;
                p=Math.max(a.seekable.start(0),Math.min(a.seekable.end(a.seekable.length-1),p));
                a.currentTime=p;return p;
            }
            return a.currentTime;
        }''',{'action':action,'seconds':seconds,'position':position,'timeout':self.start_timeout_ms})

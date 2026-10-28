"""A single controlled audio surface for radio, podcasts, and offline files."""
import os
import sys
from pathlib import Path

PLAYER_HTML = '''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Voice Companion Audio</title><style>body{font:1.25rem system-ui;padding:2rem}
button{font:inherit;margin:.35rem;padding:.6rem}audio,video{display:block;width:100%;max-height:75vh;margin:1rem 0}</style>
<h1>Voice Companion Audio</h1><p id="title">No audio selected.</p>
<audio id="audio" controls preload="none"></audio>
<button onclick="audio.play()">Play</button><button onclick="audio.pause()">Pause</button>
<button onclick="audio.pause();audio.currentTime=0">Stop</button>
<button onclick="audio.currentTime=Math.max(0,audio.currentTime-15)">Back 15 seconds</button>
<button onclick="audio.currentTime=Math.min(audio.duration||Infinity,audio.currentTime+15)">Forward 15 seconds</button>
<p id="status"></p><script>
let audio=document.getElementById('audio');
audio.onerror=()=>document.getElementById('status').textContent='This audio could not play.';
audio.onplaying=()=>document.getElementById('status').textContent='Playing.';
audio.onpause=()=>document.getElementById('status').textContent='Paused.';
</script></html>'''


def spoken_time(seconds):
    """Say a playback time in words, such as 1 minute 23 seconds."""
    try: total=int(round(float(seconds)))
    except (TypeError,ValueError,OverflowError): total=0
    if total<0: total=0
    hours,rest=divmod(total,3600);minutes,secs=divmod(rest,60)
    parts=[f'{value} {unit}'+('' if value==1 else 's') for value,unit in ((hours,'hour'),(minutes,'minute'),(secs,'second')) if value]
    return ' '.join(parts) or '0 seconds'


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)): return None
    return value if value==value and abs(value)!=float('inf') else None


def seek_message(player, seconds, noun='track'):
    """Seek by a number of seconds and say the jump and the new place.

    Returns None when the media cannot seek. At either end it says so, instead
    of naming a jump that did not fully happen.
    """
    try: before=player.status()
    except Exception: before=None
    if not isinstance(before,dict): before={}
    position=player.control('seek',seconds=seconds)
    if position is None: return None
    jump=('Moved back ' if seconds<0 else 'Moved forward ')+spoken_time(abs(seconds))+'.'
    position=_number(position)
    if position is None: return jump
    old=_number(before.get('position'));duration=_number(before.get('duration'))
    if seconds<0 and (position<=.5 or old is not None and old-position<-seconds-.5):
        return 'Moved back to the start of the '+noun+'.'
    if seconds>0 and (duration is not None and position>=duration-.5 or old is not None and position-old<seconds-.5):
        return 'Moved forward to the end of the '+noun+'.'
    if duration: return jump+' '+spoken_time(position)+' of '+spoken_time(duration)+'.'
    return jump+' Now at '+spoken_time(position)+'.'


class MediaPlayer:
    def __init__(self, folder):
        self.folder=Path(folder)
        self.playwright=self.context=self.page=None
        self.kind=None
        self.ducked=False
        self.start_timeout_ms=12000

    @property
    def kind(self):
        """'radio', 'podcast' or 'local' while something is loaded; None once
        the player window is gone (the user may close it), so no control
        reports a pause or a stop that did not happen."""
        page=self.page
        if self._kind and (page is None or page.is_closed()):self._kind=None
        return self._kind

    @kind.setter
    def kind(self,value):self._kind=value

    def start(self):
        if self.page and not self.page.is_closed(): return
        # The window was closed: release its browser and driver before starting
        # again, or the new browser finds its profile still in use.
        if self.context or self.playwright: self.close()
        from playwright.sync_api import sync_playwright
        bundled=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'browser-runtime'
        if bundled.exists(): os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(bundled)
        self.folder.mkdir(parents=True,exist_ok=True)
        html=self.folder/'audio-player.html'
        html.write_text(PLAYER_HTML,encoding='utf-8')
        try:
            import shared_playwright   # one driver per thread, shared with the web browser
            self.playwright=shared_playwright.start(self.folder)
            self.context=self.playwright.firefox.launch_persistent_context(
                str(self.folder/'profile'),headless=False,
                firefox_user_prefs={'media.autoplay.default':0})
            self.page=self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.goto(html.as_uri())
        except Exception:
            self.close()
            raise

    def close(self):
        context,driver=self.context,self.playwright
        self.context=self.page=self.playwright=None
        self.kind=None
        self.ducked=False
        for resource,method in ((context,'close'),(driver,'stop')):
            if resource is not None:
                try:getattr(resource,method)()
                except Exception:pass

    def open(self, url, title, kind):
        self.start()
        self.kind=None
        if self.page.url != (self.folder/'audio-player.html').as_uri():
            self.page.goto((self.folder/'audio-player.html').as_uri())
        result=self.page.evaluate('''async ({url,title,timeout,video,ducked}) => {
            // The lowered level is what Companion knows now; an older one may be left from a stream that stopped.
            window.voiceCompanionDucked=ducked;
            let a=document.getElementById('audio');
            if(a.tagName!==(video?'VIDEO':'AUDIO')){
                const replacement=document.createElement(video?'video':'audio');
                replacement.id='audio';replacement.controls=true;replacement.preload='none';
                a.pause();a.replaceWith(replacement);audio=replacement;a=replacement;
                a.onerror=()=>document.getElementById('status').textContent='This media could not play.';
                a.onplaying=()=>document.getElementById('status').textContent='Playing.';
                a.onpause=()=>document.getElementById('status').textContent='Paused.';
            }a.pause();a.src=url;
            a.volume=(window.voiceCompanionVolume??1)*(window.voiceCompanionDucked||1);
            document.getElementById('title').textContent=title;
            document.getElementById('status').textContent='Loading audio.';
            let timer;
            try {
                // After a stream that failed, its emptying load() can still be running. It interrupts
                // the next play() with an AbortError, and the next station then failed to start as well.
                // An interrupted start is tried again; any other refusal is final.
                window.voiceCompanionStartError='';
                const attempt=async()=>{
                    for(let i=0;i<4;i++){
                        try{await a.play();return true;}
                        catch(error){window.voiceCompanionStartError=(error&&error.name||'Error')+': '+(error&&error.message||'');
                            if(!error||error.name!=='AbortError')return false;
                            await new Promise(resolve=>setTimeout(resolve,150));}
                    }
                    return false;
                };
                const started=await Promise.race([attempt(),new Promise(resolve=>{timer=setTimeout(()=>{window.voiceCompanionStartError=window.voiceCompanionStartError||'timed out';resolve(false);},timeout)})]);
                if(!started){a.pause();a.removeAttribute('src');a.load();document.getElementById('status').textContent='This audio could not start.';}
                return started;
            } finally {clearTimeout(timer);}
        }''',{'url':url,'title':title,'timeout':self.start_timeout_ms,'video':kind=='local' and Path(url.split('?',1)[0]).suffix.lower() in ('.mp4','.m4v','.webm','.ogv','.mov'),
              'ducked':getattr(self,'duck_factor',0) if self.ducked else 0})
        if not result:
            self.kind=None
            # Why, for the log and for test failures; the user hears the plain message.
            try:self.last_start_error=self.page.evaluate('window.voiceCompanionStartError||""')
            except Exception:self.last_start_error=''
            raise OSError('This audio format could not start in the player.')
        self.kind=kind

    def adjust(self, setting, value):
        if not self.page or self.page.is_closed() or not self.kind:
            raise ValueError('Open a media file first.')
        if setting not in ('volume','speed'):
            raise ValueError('Unknown playback setting.')
        if setting=='speed' and not .5<=value<=3:
            raise ValueError('Choose a playback speed from 0.5 to 3.')
        return self.page.evaluate('''({setting,value})=>{
            const a=document.getElementById('audio');
            if(setting==='volume'){
                const volume=Math.max(0,Math.min(1,(window.voiceCompanionVolume??1)+value));
                window.voiceCompanionVolume=volume;a.volume=volume*(window.voiceCompanionDucked||1);return volume;
            }
            a.playbackRate=value;return a.playbackRate;
        }''',{'setting':setting,'value':value})

    def status(self):
        if not self.page or self.page.is_closed() or not self.kind:
            raise ValueError('Open a media file first.')
        return self.page.evaluate("() => {const a=document.getElementById('audio');return {paused:a.paused,position:a.currentTime,duration:Number.isFinite(a.duration)?a.duration:null};}")

    def set_ducked(self, ducked, factor=0.18):
        """Lower radio and podcast playback while Companion speaks.

        factor is the share of the volume that stays while lowered (0.25 is 75 percent quieter)."""
        ducked=bool(ducked)
        factor=max(0.0,min(1.0,float(factor)))
        if self.ducked==ducked and (not ducked or getattr(self,'duck_factor',None)==factor): return
        if not self.page or self.page.is_closed() or not self.kind:
            self.ducked=False
            return
        self.page.evaluate('''({ducked,factor}) => {
            window.voiceCompanionDucked=ducked?factor:0;
            document.getElementById('audio').volume=(window.voiceCompanionVolume??1)*(ducked?factor:1);
        }''',{'ducked':ducked,'factor':factor})
        self.ducked=ducked;self.duck_factor=factor

    def control(self, action, seconds=15, position=None):
        if not self.page or self.page.is_closed() or not self.kind: return None
        return self.page.evaluate('''async ({action,seconds,position,timeout}) => {
            const a=document.getElementById('audio');
            const toggling=action==='toggle';
            if(toggling){
                if(!a.paused){a.pause();return true}
                action='play';
            }
            if(action==='stop'){a.pause();try{a.currentTime=0}catch(e){};return 0}
            if(action==='pause'){a.pause();return a.currentTime}
            if(action==='play'){
                let timer;
                try {
                    const started=await Promise.race([a.play().then(()=>true,()=>false),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),timeout)})]);
                    if(!started){a.pause();throw new Error('This audio could not resume. Choose the station or episode again.');}
                    return toggling?false:a.currentTime;
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

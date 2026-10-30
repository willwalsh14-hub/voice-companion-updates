"""A single controlled audio surface for radio, podcasts, and offline files."""
import os
import sys
from pathlib import Path

PLAYER_HTML = '''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Voice Companion Audio</title><style>body{font:1.25rem system-ui;padding:2rem}
button{font:inherit;margin:.35rem;padding:.6rem}audio,video{display:block;width:100%;max-height:75vh;margin:1rem 0}</style>
<h1>Voice Companion Audio</h1><p id="title">No audio selected.</p>
<audio id="audio" controls preload="none"></audio>
<button onclick="window.voiceCompanionUserPaused=false;audio.play()">Play</button><button onclick="window.voiceCompanionUserPaused=true;audio.pause()">Pause</button>
<button onclick="window.voiceCompanionUserPaused=true;audio.pause();audio.currentTime=0">Stop</button>
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


# A playing radio stream whose place has not moved for this long has stopped.
# Shorter waits are normal buffering and are never reported.
STALL_SECONDS = 10


class StreamWatch:
    """Decides, from regular checks of the player, when a radio stream that had
    started has failed. Each play (a new station, or Play after a stop) is a new
    session, and a failure is reported once per session.

    A sample is the player's health(): paused, ended, error (a media error code
    or None), position, and user_paused (the user paused or stopped it). None
    means the player did not answer at all."""

    def __init__(self, stall_seconds=STALL_SECONDS):
        self.stall_seconds = stall_seconds
        self.session = None
        self.reported = False
        self.position = None
        self.since = None

    def check(self, session, sample, now):
        """'error', 'ended', 'stalled', 'gone' (the player stopped answering) or None."""
        if session != self.session:
            self.session, self.reported, self.position, self.since = session, False, None, None
        if self.reported:
            return None
        if sample is None:
            reason = 'gone'
        elif sample.get('user_paused') or sample.get('paused') and not sample.get('ended') and sample.get('error') is None:
            # The user paused or stopped it, or a helper pressed Pause in the
            # player window. Not a failure; the stall clock starts again later.
            self.position, self.since = None, None
            return None
        elif sample.get('error') is not None:
            reason = 'error'
        elif sample.get('loading'):
            # Still fetching the first part from a slow server: not a stall.
            self.position, self.since = None, None
            return None
        elif sample.get('ended'):
            reason = 'ended'   # a live station never ends by itself
        else:
            position = sample.get('position')
            moved = isinstance(position, (int, float)) and (self.position is None or position > self.position + 0.2)
            if moved or self.since is None:
                self.position = position if isinstance(position, (int, float)) else self.position
                self.since = now
                return None
            if now - self.since < self.stall_seconds:
                return None
            reason = 'stalled'
        self.reported = True
        return reason


# Radio, podcasts and media files start at half volume, so a screen reader is
# easy to hear over them (tester, October 2026). The user's own level is kept in
# VOLUME_FILE and used from then on.
DEFAULT_VOLUME = 0.5
VOLUME_FILE = 'playback-volume.json'
VOLUME_STEP = 0.1


def _volume(value):
    """A saved or spoken level as a number from 0 to 1, or None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return round(max(0.0, min(1.0, float(value))), 2)


class MediaPlayer:
    def __init__(self, folder):
        self.folder=Path(folder)
        self.playwright=self.context=self.page=None
        self.kind=None
        self.ducked=False
        self.volume=self._saved_volume()
        self.rate=1.0   # playback speed; each new file or stream starts at normal speed
        self.loading=False   # started, but still fetching: it plays as soon as enough has come
        self.start_timeout_ms=12000
        # What is loaded, for "What's playing?", and a number that changes with each
        # play, so a stream failure is reported once per play.
        self.title=self.url=None
        self.session=0
        self.failed=False

    def _saved_volume(self):
        try:
            import json
            saved=json.loads((self.folder/VOLUME_FILE).read_text(encoding='utf-8'))
        except (OSError,ValueError):
            return DEFAULT_VOLUME
        level=_volume(saved.get('volume') if isinstance(saved,dict) else None)
        return DEFAULT_VOLUME if level is None else level

    def _save_volume(self):
        try:
            import json
            from storage import write_text_atomic
            self.folder.mkdir(parents=True,exist_ok=True)
            write_text_atomic(self.folder/VOLUME_FILE,json.dumps({'volume':self.volume}))
        except OSError as exc:
            # The level still works until Companion closes.
            print('Playback volume could not be saved:',exc,file=sys.stderr)

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
            # The player has no window (tester, October 2026): a window had to be found with
            # Tab or Alt+Tab, and it took the keyboard from Companion. Firefox without a window
            # still plays sound; Companion's own screen has the controls.
            try:
                self.context=self._launch()
            except Exception as first:
                # The player's own profile may be damaged or still locked by a player that did not
                # close. It holds nothing of yours (the volume is kept beside it), so it is set
                # aside and the player starts once more with a new one.
                self.last_start_error='The player could not start: '+type(first).__name__+': '+str(first)[:200]
                if "doesn't exist" in str(first) or 'Executable' in str(first) or not self._set_profile_aside():
                    raise   # the player's Firefox itself is missing: a new profile cannot help
                self.context=self._launch()
            self.page=self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.goto(html.as_uri())
        except Exception:
            self.close()
            raise

    def _launch(self):
        return self.playwright.firefox.launch_persistent_context(
            str(self.folder/'profile'),headless=True,
            firefox_user_prefs={'media.autoplay.default':0})

    def _set_profile_aside(self):
        profile=self.folder/'profile'
        if not profile.exists():
            return False
        aside=self.folder/'profile-set-aside'
        try:
            if aside.exists():
                import shutil
                shutil.rmtree(aside,ignore_errors=True)
            profile.replace(aside)
            return True
        except OSError:
            return False

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
        self.failed=False
        if self.page.url != (self.folder/'audio-player.html').as_uri():
            self.page.goto((self.folder/'audio-player.html').as_uri())
        # A start cut off with an AbortError is tried once more on a freshly loaded player page:
        # on a busy computer Firefox can keep aborting the next fetch after a stream failed, even
        # with a fresh element (the 0.2.172 and 0.2.176 publish builds stopped on this).
        for attempt in range(2):
            result=self.page.evaluate('''async ({url,title,timeout,video,ducked,volume}) => {
                // The lowered level is what Companion knows now; an older one may be left from a stream that stopped.
                window.voiceCompanionDucked=ducked;window.voiceCompanionVolume=volume;
                window.voiceCompanionUserPaused=false;window.voiceCompanionFailed=false;
                let a=document.getElementById('audio');
                // After a stream that failed, its element can keep aborting the next fetch (Firefox:
                // "The fetching process for the media resource was aborted"), so the next station failed
                // too. A fresh element is used after any failed start, and when the kind changes.
                if(a.tagName!==(video?'VIDEO':'AUDIO')||window.voiceCompanionFailedStart){
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
                                await new Promise(resolve=>setTimeout(resolve,150));
                                // Start the fetch again before the next try.
                                a.src=url;a.load();}
                        }
                        return false;
                    };
                    // A slow server (a long podcast episode behind a few redirects) can take longer than
                    // the wait to send enough to play. That is not a failure: when the time is up and the
                    // player is still fetching with no error, it plays as soon as it can ('loading').
                    // Before 0.2.179 that was "could not start" (Will, October 29, 2026).
                    const playing=attempt();
                    const started=await Promise.race([playing,new Promise(resolve=>{timer=setTimeout(()=>{
                        const fetching=!a.error&&a.networkState!==3&&a.getAttribute('src');
                        if(fetching){resolve('loading');return;}
                        window.voiceCompanionStartError=window.voiceCompanionStartError||'timed out';resolve(false);},timeout)})]);
                    if(started==='loading'){
                        window.voiceCompanionLoading=true;
                        playing.then(ok=>{window.voiceCompanionLoading=false;if(!ok){window.voiceCompanionFailed=true;
                            document.getElementById('status').textContent='This audio could not start.';}});
                        return 'loading';
                    }
                    window.voiceCompanionFailedStart=!started;
                    if(!started){a.pause();a.removeAttribute('src');a.load();document.getElementById('status').textContent='This audio could not start.';}
                    return started;
                } finally {clearTimeout(timer);}
            }''',{'url':url,'title':title,'timeout':self.start_timeout_ms,'video':kind=='local' and Path(url.split('?',1)[0]).suffix.lower() in ('.mp4','.m4v','.webm','.ogv','.mov'),
                  'ducked':getattr(self,'duck_factor',0) if self.ducked else 0,'volume':self.volume})
            if result or attempt:break
            try:error=self.page.evaluate('window.voiceCompanionStartError||""')
            except Exception:error=''
            if not str(error).startswith('AbortError'):break
            self.page.goto((self.folder/'audio-player.html').as_uri())
        if not result:
            self.kind=None
            # Why, for the log and for test failures; the user hears the plain message.
            try:self.last_start_error=self.page.evaluate('window.voiceCompanionStartError||""')
            except Exception:self.last_start_error=''
            raise OSError('This audio format could not start in the player.')
        self.kind=kind
        self.loading=result=='loading'   # still fetching: plays as soon as enough has come
        self.rate=1.0
        self.title,self.url=title,url
        self.session+=1

    def streaming(self):
        """True while a live radio stream from the internet is loaded (not a saved recording)."""
        return self.kind=='radio' and str(self.url or '').startswith(('http://','https://'))

    def health(self):
        """One check of the playing media, for StreamWatch."""
        if not self.page or self.page.is_closed() or not self.kind:
            return None
        return self.page.evaluate("""() => {const a=document.getElementById('audio');
            return {paused:a.paused,ended:a.ended,error:a.error?a.error.code:(window.voiceCompanionFailed?-1:null),position:a.currentTime,
                    user_paused:!!window.voiceCompanionUserPaused,loading:!!window.voiceCompanionLoading};}""")

    def mark_failed(self):
        """The stream stopped by itself: stop it, so Play loads it again."""
        self.failed=True
        if not self.page or self.page.is_closed():return
        try:
            self.page.evaluate("""() => {const a=document.getElementById('audio');window.voiceCompanionFailed=true;
                window.voiceCompanionUserPaused=false;a.pause();}""")
        except Exception:pass

    def adjust(self, setting, value):
        if not self.page or self.page.is_closed() or not self.kind:
            raise ValueError('Open a media file first.')
        if setting not in ('volume','speed'):
            raise ValueError('Unknown playback setting.')
        if setting=='speed' and not .5<=value<=3:
            raise ValueError('Choose a playback speed from 0.5 to 3.')
        if setting=='volume':
            # A change by an amount: the level is kept here and saved for next time.
            value=_volume(self.volume+value)
            if value is None:raise ValueError('Unknown playback setting.')
        result=self.page.evaluate('''({setting,value})=>{
            const a=document.getElementById('audio');
            if(setting==='volume'){
                window.voiceCompanionVolume=value;a.volume=value*(window.voiceCompanionDucked||1);return value;
            }
            a.playbackRate=value;return a.playbackRate;
        }''',{'setting':setting,'value':value})
        if setting=='volume' and _volume(result) is not None:
            self.volume=_volume(result);self._save_volume()
        if setting=='speed' and isinstance(result,(int,float)):self.rate=float(result)
        return result

    def status(self):
        if not self.page or self.page.is_closed() or not self.kind:
            raise ValueError('Open a media file first.')
        status=self.page.evaluate("() => {const a=document.getElementById('audio');return {paused:a.paused,position:a.currentTime,duration:Number.isFinite(a.duration)?a.duration:null};}")
        if isinstance(status,dict):status['failed']=self.failed
        return status

    def set_ducked(self, ducked, factor=0.18):
        """Lower radio and podcast playback while Companion speaks.

        factor is the share of the volume that stays while lowered (0.25 is 75 percent quieter)."""
        ducked=bool(ducked)
        factor=max(0.0,min(1.0,float(factor)))
        if self.ducked==ducked and (not ducked or getattr(self,'duck_factor',None)==factor): return
        if not self.page or self.page.is_closed() or not self.kind:
            self.ducked=False
            return
        self.page.evaluate('''({ducked,factor,volume}) => {
            window.voiceCompanionDucked=ducked?factor:0;
            document.getElementById('audio').volume=(window.voiceCompanionVolume??volume)*(ducked?factor:1);
        }''',{'ducked':ducked,'factor':factor,'volume':self.volume})
        self.ducked=ducked;self.duck_factor=factor

    def control(self, action, seconds=15, position=None):
        if not self.page or self.page.is_closed() or not self.kind: return None
        result=self.page.evaluate('''async ({action,seconds,position,timeout}) => {
            const a=document.getElementById('audio');
            const toggling=action==='toggle';
            if(toggling){
                if(!a.paused){window.voiceCompanionUserPaused=true;a.pause();return true}
                action='play';
            }
            if(action==='stop'){window.voiceCompanionUserPaused=true;a.pause();try{a.currentTime=0}catch(e){};return 0}
            if(action==='pause'){window.voiceCompanionUserPaused=true;a.pause();return a.currentTime}
            if(action==='play'){
                window.voiceCompanionUserPaused=false;
                // A stream that failed, or a live stream that ended, loads again: "try again".
                if(window.voiceCompanionFailed||a.error||a.ended&&!Number.isFinite(a.duration)){
                    window.voiceCompanionFailed=false;const source=a.src;a.removeAttribute('src');a.load();a.src=source;
                }
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
        if action=='play' or action=='toggle' and result is False:
            # Playing again is a new play: a later stop is reported again.
            self.failed=False;self.session+=1
        return result

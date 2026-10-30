import time, tempfile, traceback
import web_assistant, youtube_page as yp
from web_assistant import BrowserBackend
def note(t, x):
    print('::warning title=' + t[:70] + '::' + str(x).encode('ascii','replace').decode().replace('%','%25').replace('\n','%0A')[:1500], flush=True)
for hidden in (True, False):
    web_assistant.CHROME_IN_BACKGROUND = hidden
    b = BrowserBackend(tempfile.mkdtemp(), 'chrome')
    try:
        b.goto('https://www.youtube.com/watch?v=jNQXAC9IVRw')
        yt = yp.YouTubePage(lambda: b.page)
        yt.wait_for_player()
        time.sleep(3)
        s1 = yt.state()
        try: yt.play()
        except Exception as e: note('play err hidden=%s' % hidden, repr(e))
        time.sleep(8)
        s2 = yt.state()
        extra = b.page.evaluate("()=>({vis:document.visibilityState,hasFocus:document.hasFocus(),t:(document.querySelector('video')||{}).currentTime,err:(document.querySelector('.ytp-error,yt-playability-error-supported-renderers')||{}).innerText})")
        note('hidden=%s' % hidden, 'before=%r%%0Aafter=%r%%0Aextra=%r' % ({k:s1.get(k) for k in ('paused','error','time','ad')}, {k:s2.get(k) for k in ('paused','error','time','ad')}, extra))
    except Exception:
        note('crash hidden=%s' % hidden, traceback.format_exc())
    finally:
        b.close()

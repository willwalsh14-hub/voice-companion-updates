import tempfile, time, traceback
import web_assistant, window_hiding
from web_assistant import BrowserBackend
def note(t, x):
    print('::warning title=' + t + '::' + str(x).encode('ascii','replace').decode().replace('\n','%0A')[:1200], flush=True)
web_assistant.CHROME_IN_BACKGROUND = True
for mode in ('minimized', 'parked'):
    if mode == 'minimized':
        window_hiding.Win32Desktop.park = window_hiding.Win32Desktop.tuck
        window_hiding.Win32Desktop.off_screen = lambda self, h: True
    else:
        import importlib; importlib.reload(window_hiding)
    b = BrowserBackend(tempfile.mkdtemp(), 'chrome')
    try:
        b.goto('data:text/html,<title>x</title><p>hi</p>')
        time.sleep(2)
        frames = b.page.evaluate("()=>new Promise(d=>{let n=0;const s=()=>{n++;requestAnimationFrame(s)};requestAnimationFrame(s);setTimeout(()=>d(n),2000)})")
        w = b.window_keeper.desktop.windows(b.window_keeper.pids) if getattr(b,'window_keeper',None) else 'no keeper'
        note(mode, 'frames=%s vis=%s windows=%r' % (frames, b.page.evaluate('document.visibilityState'), w))
    except Exception:
        note(mode + ' crash', traceback.format_exc())
    finally:
        b.close()

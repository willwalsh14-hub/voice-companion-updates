import sys, time, tempfile
import web_assistant
from web_assistant import BrowserBackend
web_assistant.CHROME_IN_BACKGROUND = True
for url in ('https://www.google.com/search?q=weather+chicago', 'https://www.youtube.com/watch?v=jNQXAC9IVRw'):
    b = BrowserBackend(tempfile.mkdtemp(), 'chrome')
    try:
        b.goto(url) if hasattr(b, 'goto') else None
        time.sleep(6)
        text = b.page.evaluate("document.body ? document.body.innerText.slice(0,300) : ''")
        print('::error title=diag ' + url[:40] + '::final=' + b.page.url[:120] + ' text=' + ' '.join(text.split())[:250].replace('%', '%25'))
    except Exception as e:
        print('::error title=diag ' + url[:40] + '::' + repr(e)[:300])
    finally:
        b.close()

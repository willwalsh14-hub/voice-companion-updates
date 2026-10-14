"""Open saved URLs in the actual frozen browser, without a public network."""
import tempfile
from web_assistant import WebSession,BrowserBackend

def check():
    with tempfile.TemporaryDirectory(prefix='VC bookmark check ') as folder:
        backend=BrowserBackend(folder+'/profile','firefox',headless=True)
        session=WebSession(folder,backend)
        url='https://bookmark.example/News?part=One#Top'
        try:
            for cycle in range(3):
                backend.start()
                backend.page.route('https://bookmark.example/**',lambda route:route.fulfill(status=200,content_type='text/html',body='<html lang="en"><title>Saved page</title><h1 id="Top">Bookmark opened</h1><p>Saved page text.</p></html>'))
                if cycle==0:session.favorites.add('Saved news',url)
                session.menu.start();session.menu.process('go to bookmark',session,False)
                _,action=session.menu.process('enter',session,False)
                if action!=('open',url):raise AssertionError('Bookmark address changed')
                result=session.open(action[1])
                if not session.navigation_succeeded or 'Bookmark opened' not in session.reading.text:raise AssertionError(result)
                if session.snapshot['url']!=url:raise AssertionError('Bookmark path, query or fragment was lost')
                session.leave(False)
            print('Packaged bookmark opens and browser restarts passed.',flush=True)
            return 0
        finally:backend.close()

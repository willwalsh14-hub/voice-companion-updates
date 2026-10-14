"""Open saved URLs in the actual frozen browser, without a public network."""
import tempfile
from web_assistant import WebSession,BrowserBackend

def check(app=None):
    with tempfile.TemporaryDirectory(prefix='VC bookmark check ') as folder:
        backend=BrowserBackend(folder+'/profile','firefox',headless=True)
        session=WebSession(folder,backend)
        url='https://bookmark.example/News?part=One#Top'
        try:
            for cycle in range(3):
                backend.start()
                backend.page.route('https://bookmark.example/**',lambda route:route.fulfill(status=200,content_type='text/html',body='<html lang="en"><title>Saved page</title><h1 id="Top">Bookmark opened</h1><p>Saved page text.</p></html>'))
                if cycle==0:session.favorites.add('Saved news',url)
                if app is not None:
                    from unittest.mock import patch,Mock
                    with patch.object(app,'web_session',session),patch.object(app,'speak'),patch.object(app,'APP_WINDOW',Mock()),patch.object(app,'KEYBOARD_SPEECH',None):
                        mode=app.open_browser_menu()
                        for key in ('Down','Down','Enter','Enter'):
                            mode=app.handle(app.navigation_key(key,mode),mode)
                        if mode!='web' or not session.navigation_succeeded:raise AssertionError('App keyboard bookmark did not open')
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

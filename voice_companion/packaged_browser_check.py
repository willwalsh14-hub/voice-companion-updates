"""Open saved URLs in the actual frozen guided browser, without a public network.

Embedded Edge (WebView2) is the default guided browser and is built into the
app, so the packaged check uses it."""
import tempfile
from web_assistant import WebSession

def remove_folder(folder,attempts=40,pause=0.25):
    """Delete the check's browser profile once embedded Edge lets go of it.

    WebView2's helper processes can hold profile files (BrowserMetrics) for a
    moment after the browser closes. The check passed by then, so a folder
    that stays locked is reported but does not fail the build."""
    import shutil,time
    for attempt in range(attempts):
        try:shutil.rmtree(folder);return True
        except FileNotFoundError:return True
        except OSError as error:
            if attempt==attempts-1:
                print('Note: the temporary browser profile is still in use and was left at '+str(folder)+': '+str(error),flush=True)
                return False
            time.sleep(pause)

def check(app=None):
    folder=tempfile.mkdtemp(prefix='VC bookmark check ')
    try:return _check(app,folder)
    finally:remove_folder(folder)

def _check(app,folder):
    from webview2_backend import WebView2Backend
    backend=WebView2Backend(folder+'/profile',headless=True)
    session=WebSession(folder,backend)
    url='https://bookmark.example/News?part=One#Top'
    try:
        for cycle in range(3):
            backend.start()
            backend.page.route('https://bookmark.example/**',lambda route:route.fulfill(status=200,content_type='text/html',body='<html lang="en"><title>Saved page</title><h1 id="Top">Bookmark opened</h1><p>Saved page text.</p></html>'))
            if cycle==0:session.favorites.add('Saved news',url)
            if app is not None:
                from unittest.mock import patch,Mock
                import win32com.client
                voice=win32com.client.Dispatch('SAPI.SpVoice')
                stream=win32com.client.Dispatch('SAPI.SpFileStream')
                stream.Open(folder+'/bookmark-speech.wav',3);voice.AudioOutputStream=stream
                said=[]
                with patch.object(app,'voice',voice),patch.object(app,'web_session',session),patch.object(app,'speak',lambda text='',*a,**k:said.append(str(text))),patch.object(app,'APP_WINDOW',Mock()),patch.object(app,'KEYBOARD_SPEECH',None):
                    mode=app.open_browser_menu();steps=['open: '+mode+': '+(said[-1] if said else '')]
                    for key in ('Down','Down','Enter','Enter'):
                        mode=app.handle(app.navigation_key(key,mode),mode);steps.append(key+': '+str(mode)+': '+(said[-1] if said else ''))
                    if mode!='web' or not session.navigation_succeeded:
                        raise AssertionError('App keyboard bookmark did not open (cycle '+str(cycle+1)+').\n'+'\n'.join(step[:300] for step in steps))
                voice.WaitUntilDone(1000);stream.Close()
            session.menu.start();session.menu.process('go to bookmark',session,False)
            _,action=session.menu.process('enter',session,False)
            if action!=('open',url):raise AssertionError('Bookmark address changed')
            result=session.open(action[1])
            if not session.navigation_succeeded or 'Bookmark opened' not in session.reading.text:raise AssertionError('Bookmark did not open (cycle '+str(cycle+1)+'): '+str(result)[:500])
            if session.snapshot['url']!=url:raise AssertionError('Bookmark path, query or fragment was lost')
            session.leave(False)
        print('Packaged bookmark opens and browser restarts passed.',flush=True)
        return 0
    finally:backend.close()

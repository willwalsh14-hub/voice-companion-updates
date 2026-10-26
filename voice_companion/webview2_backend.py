"""Extension-free WebView2, reached through an owned private process pipe."""
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from web_assistant import BrowserBackend,WebError

MAX_FRAME=16*1024*1024


class WebViewPipe:
    def __init__(self,profile,hidden=False,executable=None):
        from websockets.sync.server import serve
        self.profile=Path(profile);self.profile.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_urlsafe(32);self.ready=threading.Event();self.closed=threading.Event();self.error=None
        self.connection=None;self.send_lock=threading.Lock();self.client_lock=threading.Lock()
        self.page_order=[];self.active_session='main-view'
        self.pending={};self.pending_lock=threading.Lock()
        # Companion's own requests use negative ids; their answers are not sent on to the driver.
        self.own_serial=-1000;self.own_replies={}
        # Download news (Host.download) goes here, not to the automation driver.
        self.on_download=None;self.on_closed=None
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
        host=Path(executable) if executable else root/'webview2'/'VoiceCompanion-WebView2.exe'
        if not host.is_file():raise WebError('The embedded Edge host is missing. Reinstall the complete Voice Companion build.')
        logger=logging.Logger('WebView2 transport');logger.disabled=True
        self.server=serve(self._websocket,'127.0.0.1',0,process_request=self._authenticate,max_size=MAX_FRAME,compression=None,logger=logger)
        self.endpoint='ws://127.0.0.1:'+str(self.server.socket.getsockname()[1])+'/'+self.token
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        env=os.environ.copy()
        # App settings must not expose a debugging listener or reuse another profile.
        for name in list(env):
            if name.startswith('WEBVIEW2_') or name in ('PYTHONHOME','PYTHONPATH'):env.pop(name,None)
        try:self.process=subprocess.Popen([str(host),str(self.profile.resolve()),*(['--test-hidden'] if hidden else [])],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,encoding='utf-8',text=True,bufsize=1,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except Exception:self.server.shutdown();raise
        threading.Thread(target=self._read,daemon=True).start()
        threading.Thread(target=self._watchdog,daemon=True).start()
        if not self.ready.wait(25):self.close();raise WebError('Embedded Edge could not start. Check that Microsoft Edge WebView2 Runtime is installed.')
        if self.error:self.close();raise WebError('Embedded Edge could not start: '+self.error)

    def _authenticate(self,connection,request):
        if request.headers.get_all('Origin') or not hmac.compare_digest(request.path,'/'+self.token):return connection.respond(HTTPStatus.FORBIDDEN,'Browser connection denied.\n')

    def _send(self,message):
        if os.getenv('VOICE_COMPANION_TRACE_WEBVIEW2'):print('CDP OUT',message.get('id'),message.get('method'),message.get('sessionId'),file=sys.stderr,flush=True)
        encoded=json.dumps(message,ensure_ascii=False)
        if len(encoded.encode('utf-8'))>MAX_FRAME:raise ValueError('Browser message too large.')
        with self.send_lock:
            if self.process.poll() is not None:raise OSError('Embedded browser closed.')
            if isinstance(message.get('id'),int) and message['id']>=0:
                with self.pending_lock:self.pending[(message.get('sessionId'),message['id'])]=(time.monotonic(),message.get('method'))
            self.process.stdin.write(encoded+'\n');self.process.stdin.flush()

    def _watchdog(self):
        while not self.closed.wait(1):
            with self.pending_lock:expired=next((method for started,method in self.pending.values() if time.monotonic()-started>35),None)
            if expired:
                self.error='Embedded Edge stopped responding. The action will not be repeated.'
                if os.getenv('VOICE_COMPANION_TRACE_WEBVIEW2'):print('CDP TIMEOUT',expired,file=sys.stderr,flush=True)
                self.closed.set()
                if self.connection:
                    try:self.connection.close()
                    except Exception:pass
                return

    def _read(self):
        try:
            while not self.closed.is_set():
                line=self.process.stdout.readline(MAX_FRAME+1)
                if not line:break
                if len(line)>MAX_FRAME:break
                value=json.loads(line)
                if 'id' in value:
                    with self.pending_lock:self.pending.pop((value.get('sessionId'),value['id']),None)
                if os.getenv('VOICE_COMPANION_TRACE_WEBVIEW2'):print('CDP IN',value.get('id'),value.get('method'),value.get('sessionId'),'error' if 'error' in value else '',file=sys.stderr,flush=True)
                method=value.get('method');params=value.get('params',{})
                if method=='Host.activeTab':self.active_session=params.get('sessionId');continue
                if method=='Host.download':
                    handler=self.on_download
                    if handler is not None:
                        try:handler(params if isinstance(params,dict) else {})
                        except Exception:pass
                    continue
                if method=='Target.attachedToTarget':
                    session=params.get('sessionId','')
                    if (session=='main-view' or session.startswith('view-')) and session not in self.page_order:self.page_order.append(session)
                if method=='Target.detachedFromTarget':
                    session=params.get('sessionId')
                    if session in self.page_order:self.page_order.remove(session)
                    # Closing a tab cancels its pending evaluations. Native
                    # WebView2 does not always complete those API tasks after
                    # disposal; they must not trip the live-tab watchdog.
                    with self.pending_lock:
                        self.pending={key:value for key,value in self.pending.items() if key[0]!=session}
                if isinstance(value.get('id'),int) and value['id']<0:
                    waiting=self.own_replies.get(value['id'])
                    if waiting is not None:waiting.append(value);self.own_replies.pop(value['id'],None)
                    continue
                if value.get('method')=='Host.ready':self.ready.set();continue
                if value.get('method')=='Host.error':self.error=value.get('params',{}).get('message','Browser initialization failed.');self.ready.set();continue
                if self.connection:
                    # Forward the host's JSON text as it is; encoding it again
                    # doubled the work for every large page snapshot.
                    try:self.connection.send(line.rstrip('\r\n'))
                    except Exception:break
        except (OSError,ValueError):pass
        finally:
            self.closed.set()
            if self.on_closed is not None:
                try:self.on_closed()
                except Exception:pass
            try:self.process.stdout.close()
            except OSError:pass
            if self.connection:
                try:self.connection.close()
                except Exception:pass

    def _websocket(self,connection):
        if not self.client_lock.acquire(blocking=False):connection.close();return
        self.connection=connection
        try:
            for raw in connection:
                value=json.loads(raw)
                if not isinstance(value,dict) or not isinstance(value.get('method'),str):break
                self._send(value)
        except (OSError,ValueError):pass
        finally:self.connection=None;self.client_lock.release()

    def focus(self):self._send({'id':-1,'method':'Host.focus'})

    def post(self,method,params=None):
        """Send Companion's own request without waiting for the answer.

        Used by download controls, which can be sent from this pipe's reading
        thread; waiting there for an answer it must read itself would stall."""
        with self.pending_lock:
            self.own_serial-=1;ident=self.own_serial
        self._send({'id':ident,'method':method,'params':params or {}})

    def call(self,method,params=None,session=None,timeout=5):
        """Ask the embedded browser one protocol question for Companion itself."""
        with self.pending_lock:
            self.own_serial-=1;ident=self.own_serial
        answer=[];self.own_replies[ident]=answer
        message={'id':ident,'method':method,'params':params or {}}
        if session:message['sessionId']=session
        self._send(message)
        deadline=time.monotonic()+timeout
        while not answer and time.monotonic()<deadline and not self.closed.is_set():time.sleep(.01)
        self.own_replies.pop(ident,None)
        if not answer:raise WebError('Embedded Edge did not answer.')
        if 'error' in answer[0]:raise WebError(str(answer[0]['error'].get('message','Embedded Edge refused the request.')))
        return answer[0].get('result',{})

    def close(self):
        if getattr(self,'process',None):
            try:
                self.process.stdin.close();self.process.wait(timeout=8)
            except (OSError,subprocess.TimeoutExpired):
                if self.process.poll() is None:self.process.terminate();self.process.wait(timeout=5)
        self.closed.set()
        if self.connection:
            try:self.connection.close()
            except Exception:pass
        self.server.shutdown()


class WebView2Backend(BrowserBackend):
    def __init__(self,folder,headless=False,executable=None,driver=None):
        super().__init__(folder,'webview2',headless);self.host=None;self.remote_browser=None;self.executable=executable;self.external_driver=driver;self.start_failure=None

    START_ATTEMPTS=2

    def start(self):
        if self.page and not self.page.is_closed() and self.host and not self.host.closed.is_set():self.sync_active_tab();return
        self.close()
        # The first start on a computer can be slow while WebView2 prepares its
        # profile. Try once more before giving up, so the user is not moved to
        # Firefox for a one-time delay. The real reason is kept for the logs.
        failure=None
        for attempt in range(self.START_ATTEMPTS):
            try:
                self._start_once();self.start_failure=None;return
            except Exception as exc:
                failure=exc;self.start_failure=type(exc).__name__+': '+str(exc)[:500]
                self.close()
        raise WebError('Embedded Edge could not connect. Retry, or choose another guided browser in Options. Check that Microsoft Edge WebView2 Runtime is installed.') from failure

    def _start_once(self):
        from playwright.sync_api import sync_playwright
        self.host=WebViewPipe(self.folder,self.headless,self.executable)
        self._link_downloads(self.host)
        import shared_playwright
        self.playwright=self.external_driver or shared_playwright.start(self.folder)
        self.remote_browser=self.playwright.chromium.connect_over_cdp(self.host.endpoint,timeout=20000,no_defaults=True)
        self.context=self.remote_browser.contexts[0]
        self.page=self.context.pages[0]
        self.page.set_default_timeout(10000)

    def _link_downloads(self,host):
        """Embedded Edge's downloads report into the shared download list."""
        import downloads
        model=downloads.manager()
        controller=downloads.EdgeDownloads(host)
        host.on_download=lambda params:model.report('edge',params.get('id',''),**downloads.edge_report(params))
        host.on_closed=lambda:model.detach('edge',controller)
        self.download_controller=controller
        model.attach('edge',controller)

    # Embedded Edge cannot open a second protocol session for Companion; its
    # history list is asked for through the host instead (_navigation_history).
    CDP_HISTORY=False

    def _navigation_history(self):
        host=self.host
        if not host or host.closed.is_set() or not self.context:return None
        pages=self.context.pages;order=list(host.page_order)
        session=order[pages.index(self.page)] if len(pages)==len(order) and self.page in pages else host.active_session
        history=host.call('Page.getNavigationHistory',{},session)
        return history,lambda entry:host.call('Page.navigateToHistoryEntry',{'entryId':entry},session),lambda:None

    def refocus(self):
        if not self.host or self.host.closed.is_set():return False
        if self.page and not self.page.is_closed():self.page.bring_to_front()
        if not self.headless:self.host.focus()
        return True

    def sync_active_tab(self):
        if not self.host or not self.context:return False
        pages=self.context.pages;order=list(self.host.page_order)
        if len(pages)!=len(order) or self.host.active_session not in order:return False
        target=pages[order.index(self.host.active_session)]
        changed=self.page is not target;self.page=target;return changed

    def close(self):
        driver,host,remote=self.playwright,self.host,self.remote_browser
        self.page=self.context=self.playwright=self.remote_browser=self.host=None
        controller=getattr(self,'download_controller',None)
        if controller is not None:
            # Downloads in embedded Edge end with its window.
            self.download_controller=None
            import downloads
            downloads.manager().detach('edge',controller)
        # WebView2 owns its process. Browser.close can hang a CDP-attached view;
        # stopping the transport and closing our host releases only this profile.
        if host:host.close()
        if remote:
            try:remote.close()
            except Exception:pass
        if driver and self.external_driver is None:
            try:driver.stop()
            except Exception:pass
